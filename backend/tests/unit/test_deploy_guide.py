"""S4.2: docs/deploy/guide.md is kept honest by tests. Every `make` target and script it names
exists, every Terraform output and variable it quotes is declared, every repository variable it
tells you to set is one a workflow reads (and the other way round), every runbook link lands on a
heading, each step has the shape the sprint asks for, and it holds no account id or secret."""

import re
from pathlib import Path

REPO = Path(__file__).parents[3]
GUIDE = REPO / "docs" / "deploy" / "guide.md"
RUNBOOK = REPO / "docs" / "runbook.md"
MAKEFILE = REPO / "Makefile"
TERRAFORM = REPO / "infra" / "terraform"


def guide() -> str:
    return GUIDE.read_text()


def make_targets() -> set[str]:
    targets: set[str] = set()
    for line in MAKEFILE.read_text().splitlines():
        match = re.match(r"^([a-zA-Z0-9_ -]+?)\s*:(?!=)", line)
        if match:
            targets.update(match.group(1).split())
    return targets


def code() -> str:
    """Just the code: fenced blocks and inline spans, not the prose around them."""
    text = guide()
    fenced = re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
    inline = re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.DOTALL))
    return "\n".join([*fenced, *inline])


def test_every_make_target_the_guide_names_exists() -> None:
    named = set(re.findall(r"\bmake[ \t]+([a-z][a-z0-9-]*)", code()))
    assert len(named) >= 15, named
    missing = named - make_targets()
    assert not missing, f"the guide names make targets that don't exist: {sorted(missing)}"


def test_every_script_and_path_the_guide_names_exists() -> None:
    text = guide()
    paths = set(
        re.findall(r"\b(?:scripts|infra|docs|frontend|backend)/[A-Za-z0-9_./-]*[A-Za-z0-9_]", text)
    )
    assert len(paths) >= 12, paths
    generated = {"frontend/playwright-report"}  # made by a run, not in the repository
    for raw in sorted(paths):
        if "<" in raw or "*" in raw or raw in generated or raw.endswith(".tfvars"):
            continue
        path = REPO / raw.rstrip("/.")
        assert path.exists(), f"the guide names {raw}, which doesn't exist"


def test_every_terraform_output_the_guide_quotes_is_declared() -> None:
    declared = set(
        re.findall(
            r'output\s+"([a-z_]+)"', "\n".join(p.read_text() for p in TERRAFORM.rglob("outputs.tf"))
        )
    )
    quoted = set(
        re.findall(
            r"(?:terraform|tf\.sh (?:platform|app)) output(?: -raw)? ([a-z][a-z_]+)\b", code()
        )
    )
    assert {
        "elastic_ip",
        "instance_id",
        "dns_record",
        "deploy_role_arn",
        "data_volume_id",
    } <= quoted
    assert not quoted - declared, f"not declared in any outputs.tf: {sorted(quoted - declared)}"


def test_every_terraform_variable_the_guide_names_is_declared() -> None:
    declared = set(
        re.findall(
            r'variable\s+"([a-z_]+)"',
            "\n".join(p.read_text() for p in TERRAFORM.rglob("variables.tf")),
        )
    )
    for name in (
        "domain_name",
        "app_subdomain",
        "alert_email",
        "github_repo",
        "repo_ref",
        "create_oidc_provider",
    ):
        assert f"`{name}`" in guide() or f"{name} =" in guide(), f"the guide should mention {name}"
        assert name in declared, name


def test_the_repository_variables_the_guide_sets_are_the_ones_the_workflows_read() -> None:
    used: set[str] = set()
    for workflow in (REPO / ".github" / "workflows").glob("*.yml"):
        used |= set(re.findall(r"vars\.([A-Z_]+)", workflow.read_text()))
    in_table = set(
        re.findall(
            r"^\s*\|\s*`([A-Z][A-Z_]+)`\s*\|",
            guide().split("### 6c.")[1].split("\n---\n")[0],
            re.MULTILINE,
        )
    )
    in_table -= {"AWS_PROFILE"}
    assert in_table, "the guide lists no repository variables"
    assert in_table - used == set(), f"set but never read: {sorted(in_table - used)}"
    assert used - in_table - {"COMMIT_AUTHORS"} == set(), (
        f"read but not in the guide: {sorted(used - in_table)}"
    )


def test_every_runbook_link_lands_on_a_heading() -> None:
    headings = {
        re.sub(r"[^a-z0-9 -]", "", line.lstrip("# ").lower()).replace(" ", "-")
        for line in RUNBOOK.read_text().splitlines()
        if line.startswith("## ")
    }
    anchors = set(re.findall(r"runbook\.md#([a-z0-9-]+)", guide()))
    assert len(anchors) >= 5, anchors
    assert not anchors - headings, f"no such runbook heading: {sorted(anchors - headings)}"


def test_each_step_has_the_shape_the_sprint_asks_for() -> None:
    text = guide()
    steps = re.split(r"(?m)^## Step ", text)[1:]
    assert len(steps) == 12
    for step in steps:
        title = step.splitlines()[0]
        body = step.split("\n# Part", 1)[0]
        assert "- [ ]" in body, f"step {title}: no box to tick"
        if not title.startswith("1."):
            assert re.search(r"\*\*Check", body), f"step {title}: no Check"
            assert re.search(r"\*\*Why", body), f"step {title}: no Why"
    assert len(re.findall(r"(?m)^# Part [1-4]", text)) == 4
    for section in ("# Troubleshooting", "# Glossary"):
        assert section in text


def test_the_guide_says_which_steps_draw_credit() -> None:
    assert text_mentions("Draws credit")
    assert "$18" in guide()
    assert "Checked on **30 September 2026**" in guide()


def text_mentions(phrase: str) -> bool:
    return phrase in guide()


def test_the_guide_holds_placeholders_only() -> None:
    text = guide()
    numbers = set(re.findall(r"\b\d{12}\b", text)) - {"123456789012"}  # AWS's own example id
    assert not numbers, f"an AWS account id: {numbers}"
    assert not re.search(r"AKIA[A-Z0-9]{12,}|sk-[A-Za-z0-9_-]{16,}|pk-lf-[A-Za-z0-9]{8,}", text)
    assert "<domain>" in text
    assert "<account-id>" in text


def test_the_guide_covers_every_troubleshooting_case_the_sprint_lists() -> None:
    text = guide()
    for case in (
        "DNS not delegated, or the Cloudflare record is proxied",
        "The SSM agent isn't online",
        "`AssumeRoleWithWebIdentity` denied",
        "An image pull is denied",
        "The host is out of memory",
        "A credit task's $20 didn't show up",
    ):
        assert case in text, case


def test_the_guide_has_every_exercise_and_part() -> None:
    text = guide()
    for exercise in (
        "Deploy a change",
        "Roll back, and roll forward",
        "Flip the kill switch",
        "Restore last night's dump",
        "Find one turn's logs",
        "Apply updates and reboot",
        "Stop the host and start it again",
        "Read the Credits page",
    ):
        assert exercise in text, exercise
    assert "tearing it all down" in text.lower()
    assert "Oracle" in text
    assert "VPS fallback" in text
