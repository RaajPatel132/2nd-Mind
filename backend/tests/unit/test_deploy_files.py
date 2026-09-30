"""S4.3 to S4.5: the deployment files, checked without an account or a host. Production runs the
rehearsal's compose file (they differ in values, never in services), the containers and the
edge know nothing about AWS, the parameters the deploy reads are in the configuration matrix,
and no workflow holds a long-lived AWS key."""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from secondmind.config import Settings

REPO = Path(__file__).parents[3]
COMPOSE = REPO / "compose.prodlike.yaml"
HOST = REPO / "infra" / "host"
WORKFLOWS = REPO / ".github" / "workflows"
MATRIX = REPO / "docs" / "deploy" / "config.md"
PROD_EXAMPLE = REPO / ".env.prod.example"

SERVICES = {"postgres", "redis", "migrate", "api", "worker", "web", "caddy"}


def compose() -> dict[str, object]:
    return dict(yaml.safe_load(COMPOSE.read_text()))


def services() -> dict[str, dict[str, object]]:
    return dict(compose()["services"])  # type: ignore[arg-type]


# ------------------------------------------------------------------ one compose file


def test_the_compose_file_runs_exactly_the_production_services() -> None:
    assert set(services()) == SERVICES


def test_the_images_of_the_app_take_their_registry_and_tag_from_the_environment() -> None:
    for name in ("migrate", "api", "worker"):
        image = str(services()[name]["image"])
        assert image.startswith(
            "${IMAGE_REGISTRY:+${IMAGE_REGISTRY}/}secondmind-api:${IMAGE_TAG:?"
        ), name
    web = str(services()["web"]["image"])
    assert web.startswith("${IMAGE_REGISTRY:+${IMAGE_REGISTRY}/}secondmind-web:${IMAGE_TAG:?")
    text = COMPOSE.read_text()
    assert not re.search(r"ghcr\.io/[a-z]", text), "the registry is a value, not written in"


def test_the_rehearsal_and_the_deploy_script_use_that_one_file_and_no_overlay() -> None:
    makefile = (REPO / "Makefile").read_text()
    assert "-f compose.prodlike.yaml" in makefile
    assert "-f compose.yaml -f compose.prodlike.yaml" not in makefile
    deploy = (HOST / "deploy.sh").read_text()
    assert "compose.prodlike.yaml" in deploy
    other = [p for p in (REPO / "infra").rglob("*") if re.match(r"compose.*\.ya?ml$", p.name)]
    assert not other, (
        f"a second compose file would let production drift from the rehearsal: {other}"
    )


def test_the_memory_limits_add_up_to_about_a_gigabyte_and_a_half() -> None:
    total = 0
    for name, spec in services().items():
        limit = spec["deploy"]["resources"]["limits"]["memory"]  # type: ignore[index]
        assert str(limit).endswith("M"), name
        total += int(str(limit)[:-1])
    assert 1_400 <= total <= 1_700, f"limits add up to {total} MiB; a 2 GB host needs about 1.6 GB"


def test_the_app_containers_are_locked_down() -> None:
    for name in ("migrate", "api", "worker", "web", "caddy"):
        spec = services()[name]
        assert spec["read_only"] is True, name
        assert "no-new-privileges:true" in spec["security_opt"], name  # type: ignore[operator]
        assert spec["cap_drop"] == ["ALL"], name
    text = COMPOSE.read_text()
    assert "docker.sock" not in text
    assert "privileged" not in text


def test_the_stack_never_reaches_for_a_cloud_credential() -> None:
    text = COMPOSE.read_text()
    assert not re.search(r"AWS_|amazonaws|169\.254", text)


def test_production_is_one_environment_with_no_staging_left_in_the_compose_file() -> None:
    text = COMPOSE.read_text()
    assert "ENV: production" in text
    assert "staging" not in text.lower()


def test_every_variable_the_compose_file_reads_is_in_the_matrix() -> None:
    names = set(re.findall(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)", COMPOSE.read_text()))
    documented = set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]*)`", MATRIX.read_text(), re.MULTILINE))
    assert not names - documented, f"add {sorted(names - documented)} to docs/deploy/config.md"


def test_every_passed_through_setting_is_a_real_setting() -> None:
    env = dict(services()["api"]["environment"])  # type: ignore[arg-type]
    fields = {name.upper() for name in Settings.model_fields}
    compose_only = {"APP_DB_PASSWORD", "DATABASE_MIGRATION_URL"}  # read by the migration tools
    unknown = set(env) - fields - compose_only
    assert not unknown, f"typo? {sorted(unknown)} are not Settings fields"


# ------------------------------------------------------------------ the edge and the host


def test_the_edge_has_no_domain_written_into_it() -> None:
    for path in (REPO / "infra" / "edge").rglob("*"):
        if path.is_file():
            text = path.read_text()
            assert not re.search(r"\b[a-z0-9-]+\.(com|dev|app|io|net|org|in)\b", text), path
    site = (REPO / "infra" / "edge" / "sites" / "2nd-mind.caddy").read_text()
    assert site.lstrip().startswith("#") or "{$APP_HOST}" in site
    assert "{$APP_HOST} {" in site


def test_each_project_installs_its_own_site_file() -> None:
    caddyfile = (REPO / "infra" / "edge" / "Caddyfile").read_text()
    assert "import /etc/caddy/sites/*.caddy" in caddyfile
    assert list((REPO / "infra" / "edge" / "sites").glob("*.caddy"))


def test_a_deploy_is_a_blip_the_edge_holds_requests_while_the_api_restarts() -> None:
    site = (REPO / "infra" / "edge" / "sites" / "2nd-mind.caddy").read_text()
    assert "lb_try_duration 15s" in site
    assert "health_uri /readyz" in site
    assert "flush_interval -1" in site  # the event stream isn't held back
    assert "encode @static" in site  # and isn't compressed


def test_the_host_scripts_outside_aws_make_no_cloud_calls() -> None:
    portable = [HOST / n for n in ("setup.sh", "deploy.sh", "backup.sh", "reboot-if-needed.sh")]
    portable += list((HOST / "systemd").iterdir())
    for path in portable:
        text = path.read_text()
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert not re.search(r"\baws\s+(ssm|s3|s3api|ec2|sts|logs)\b", code), path
        assert "amazonaws.com" not in code, path


def test_the_backend_and_images_know_nothing_about_aws() -> None:
    for path in (REPO / "backend" / "src").rglob("*.py"):
        text = path.read_text()
        assert not re.search(r"\bboto3\b|botocore|amazonaws", text), path
    for name in ("backend/Dockerfile", "frontend/Dockerfile"):
        assert not re.search(r"aws", (REPO / name).read_text(), re.IGNORECASE), name


def test_deploy_runs_the_migration_before_the_new_release_and_can_skip_it() -> None:
    deploy = (HOST / "deploy.sh").read_text()
    order = [deploy.index(marker) for marker in ('log "pull"', 'log "migrate"', 'log "up"')]
    assert order == sorted(order)
    assert "--no-migrate" in deploy
    assert "nothing was changed" in deploy  # a failed migration leaves the old release serving
    assert "putting $PREVIOUS back" in deploy  # and a release that isn't ready is undone


def test_the_first_boot_script_only_fetches_the_repo_and_runs_the_two_setups() -> None:
    text = (HOST / "aws" / "user-data.sh.tftpl").read_text()
    assert "infra/host/setup.sh" in text
    assert "infra/host/aws/setup-aws.sh" in text
    assert len(text.encode()) < 4_000, (
        "user data has a 16 KB limit, and the logic belongs in the repo"
    )


# ------------------------------------------------------------------ the parameters


def _sections() -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"secret": [], "plain": []}
    current = ""
    for line in PROD_EXAMPLE.read_text().splitlines():
        if line.startswith("# [secret]"):
            current = "secret"
        elif line.startswith("# [plain]"):
            current = "plain"
        elif re.match(r"^[A-Z][A-Z0-9_]*=", line):
            sections[current].append(line.split("=", 1)[0])
    return sections


def _matrix() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in MATRIX.read_text().splitlines():
        match = re.match(r"^\|\s*`([^`]+)`\s*\|(.*)\|\s*$", line)
        if match:
            rows[match.group(1)] = [c.strip() for c in match.group(2).split("|")]
    return rows


def test_every_parameter_the_deploy_reads_is_in_the_matrix_with_the_right_source() -> None:
    sections, rows = _sections(), _matrix()
    for kind, source in (("secret", "secret"), ("plain", "ssm")):
        for name in sections[kind]:
            assert name in rows, f"{name} is written to SSM but not in docs/deploy/config.md"
            assert rows[name][-1] == source, f"{name}: {kind} parameters have source `{source}`"


def test_every_secret_in_the_matrix_is_read_from_ssm() -> None:
    sections, rows = _sections(), _matrix()
    built_by_compose = {"DATABASE_URL", "REDIS_URL", "DATABASE_MIGRATION_URL"}
    for name, cells in rows.items():
        if cells[-1] == "secret" and name not in built_by_compose:
            assert name in sections["secret"], f"{name} is a secret but nothing writes it to SSM"
        if cells[-1] == "ssm":
            assert name in sections["plain"], f"{name} is source ssm but nothing writes it"
    assert not set(sections["secret"]) & set(sections["plain"])


def test_the_example_holds_no_value_that_looks_real() -> None:
    text = PROD_EXAMPLE.read_text()
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}|pk-lf-[A-Za-z0-9]{8,}|AKIA[A-Z0-9]{12,}", text)
    for name in _sections()["secret"]:
        assert re.search(rf"^{name}=$", text, re.MULTILINE), f"{name} must be empty in the example"


def _run_prod_secrets(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "prod.env"
    env_file.write_text(content)
    return subprocess.run(  # noqa: S603
        [str(REPO / "scripts" / "prod-secrets.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "PROD_SECRETS_FILE": str(env_file),
            "PROD_SECRETS_DRY_RUN": "1",
        },
    )


def _full_file() -> str:
    secrets = "\n".join(f"{n}=value-{i}" for i, n in enumerate(_sections()["secret"]))
    return f"{secrets}\nAPP_HOST=2nd-mind.example.test\nGUESTS_OPEN=false\n"


def test_prod_secrets_checks_the_file_before_it_writes_anything(tmp_path: Path) -> None:
    ok = _run_prod_secrets(tmp_path, _full_file())
    assert ok.returncode == 0, ok.stderr
    assert "would write /secondmind/prod/ANTHROPIC_API_KEY" in ok.stdout
    assert "SecureString" in ok.stdout
    assert "would write /secondmind/prod/APP_HOST" in ok.stdout
    typo = _run_prod_secrets(tmp_path, _full_file() + "ANTHROPIC_APIKEY=x\n")
    assert typo.returncode == 2
    assert "not in .env.prod.example" in typo.stderr
    quote = _run_prod_secrets(tmp_path, _full_file() + "SESSION_SECRET=it's\n")
    assert quote.returncode == 2
    missing = _run_prod_secrets(tmp_path, "APP_HOST=2nd-mind.example.test\n")
    assert missing.returncode == 2
    assert "is a secret and has no value" in missing.stderr


def test_prod_secrets_never_puts_a_value_in_a_command_line() -> None:
    text = (REPO / "scripts" / "prod-secrets.sh").read_text()
    assert '--value "file://$tmp"' in text
    assert '--value "$val"' not in text


# ------------------------------------------------------------------ no long-lived AWS keys


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_every_workflow_is_valid_yaml_and_holds_no_long_lived_aws_key(path: Path) -> None:
    yaml.safe_load(path.read_text())
    text = path.read_text()
    assert not re.search(r"AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN", text)
    assert not re.search(r"secrets\.AWS", text, re.IGNORECASE)
    if "configure-aws-credentials" in text:
        assert "role-to-assume" in text
        assert "id-token: write" in text


def test_there_is_a_deploy_workflow_that_assumes_a_role_and_holds_no_keys() -> None:
    deploy = (WORKFLOWS / "deploy.yml").read_text()
    assert "role-to-assume" in deploy
    workflow = yaml.safe_load(deploy)
    assert workflow["jobs"]["deploy"]["environment"]["name"] == "production"
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["permissions"]["id-token"] == "write"


# ------------------------------------------------------------------ Terraform: credits only

TERRAFORM = REPO / "infra" / "terraform"


def _tf_files() -> list[Path]:
    return sorted(p for p in TERRAFORM.rglob("*.tf") if ".terraform" not in p.parts)


def test_terraform_declares_none_of_what_the_plan_leaves_out() -> None:
    # The credits-only rule (ADR-0035): no NAT gateway, load balancer, RDS, ECR or CloudFront,
    # and nothing else that adds a paid service. There is no key pair (no SSH), no stored access
    # key (CI uses OIDC), and no Secrets Manager or Route 53 (SSM parameters and Cloudflare).
    forbidden = re.compile(
        r'resource\s+"(aws_nat_gateway|aws_lb\w*|aws_alb\w*|aws_elb\w*|aws_db_\w+|aws_rds_\w+|'
        r"aws_ecr_\w+|aws_cloudfront_\w+|aws_elasticache_\w+|aws_ecs_\w+|aws_eks_\w+|aws_key_pair|"
        r"aws_iam_access_key|aws_secretsmanager_\w+|aws_route53_\w+|aws_lambda_\w+|aws_wafv2_\w+|"
        r'aws_kms_key|aws_efs_\w+)"'
    )
    found = {
        p.relative_to(TERRAFORM).as_posix(): forbidden.findall(p.read_text()) for p in _tf_files()
    }
    assert not {k: v for k, v in found.items() if v}, found


def test_the_instance_has_no_key_pair_and_no_public_address_of_its_own() -> None:
    # A mocked plan can't see an argument that was left out (the provider fills it in), so the
    # sources are checked: no key_name, so no SSH, and access is by Session Manager.
    for path in _tf_files():
        code = "\n".join(
            line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
        )
        assert "key_name" not in code, path
        assert "associate_public_ip_address" not in code, path


def test_terraform_pins_the_provider_and_keeps_state_in_s3_with_a_lockfile() -> None:
    for root in ("platform", "app"):
        versions = (TERRAFORM / root / "versions.tf").read_text()
        assert 'required_version = ">= 1.10"' in versions
        assert 'version = "~> 6.66"' in versions
        assert "use_lockfile = true" in versions
        assert 'backend "s3"' in versions
        assert (TERRAFORM / root / ".terraform.lock.hcl").exists(), f"commit {root}'s lock file"


def test_no_domain_is_written_into_terraform() -> None:
    for path in _tf_files() + list(TERRAFORM.rglob("*.tftest.hcl")):
        text = path.read_text()
        for domain in re.findall(r"\b[a-z0-9-]+\.(?:com|dev|app|io|net|org|in)\b", text):
            # names of AWS's own endpoints and the GitHub and Ubuntu addresses are not ours
            assert domain.split(".")[0] in {
                "amazonaws",
                "githubusercontent",
                "github",
                "example",
            }, (
                path,
                domain,
            )
    variables = (TERRAFORM / "platform" / "variables.tf").read_text()
    assert re.search(r'variable "domain_name" \{[^}]*\}', variables, re.DOTALL)
    assert "default" not in re.search(
        r'variable "domain_name" \{.*?\n\}', variables, re.DOTALL
    ).group(0)  # type: ignore[union-attr]
    assert 'default     = "2nd-mind"' in variables  # app_subdomain


def test_every_resource_is_tagged_with_project_and_env() -> None:
    for root in ("platform", "app"):
        main = (TERRAFORM / root / "main.tf").read_text()
        assert 'project = "secondmind"' in main
        assert 'env     = "prod"' in main
        assert "default_tags" in main


def test_the_two_roots_agree_on_the_backup_bucket_name() -> None:
    platform = (TERRAFORM / "platform" / "main.tf").read_text()
    app = (TERRAFORM / "app" / "main.tf").read_text()
    assert (
        'backup_bucket_name = "secondmind-backups-${data.aws_caller_identity.current.account_id}"'
        in platform
    )
    assert 'backup_bucket_name   = "secondmind-backups-${local.account_id}"' in app


def test_the_terraform_tests_cover_what_the_sprint_says_must_not_change() -> None:
    tests = "\n".join(p.read_text() for p in TERRAFORM.rglob("*.tftest.hcl"))
    for phrase in (
        "only tcp 80 and 443 may be open",
        "IMDSv2 must be required",
        "a hop limit of 1",
        "the data volume must be encrypted",
        "the root volume must be encrypted",
        "the bucket must be encrypted at rest",
        "the deploy role trusts exactly repo:owner/repo:environment:production",
        "no statement may have Resource *",
        "the host is a t4g.small",
        "net cost is capped at $1 a month",
        "gross cost is capped at $25 a month",
    ):
        assert phrase in tests, phrase


def test_every_shell_script_parses() -> None:
    scripts = [
        *sorted((REPO / "scripts").glob("*.sh")),
        *sorted(HOST.rglob("*.sh")),
        REPO / "infra" / "terraform" / "check.sh",
    ]
    assert len(scripts) >= 15
    for path in scripts:
        # macOS ships bash 3.2: the scripts a person runs on their laptop must parse there too,
        # which means no associative arrays and no `${var,,}`; the host scripts are for Ubuntu.
        result = subprocess.run(  # noqa: S603
            ["/bin/bash", "-n", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
        assert path.stat().st_mode & 0o111, f"{path} is not executable"


def test_the_scripts_a_person_runs_on_a_mac_avoid_bash_4_features() -> None:
    mac = [
        "prod-secrets.sh",
        "prod-ssm.sh",
        "host.sh",
        "deploy.sh",
        "smoke-prod.sh",
        "restore-local.sh",
        "tf.sh",
        "tf-bootstrap.sh",
        "prodlike-env.sh",
        "check-deployed.sh",
    ]
    for name in mac:
        text = (REPO / "scripts" / name).read_text()
        assert "declare -A" not in text, name
        assert "mapfile" not in text, name
        assert "readarray" not in text, name
        assert not re.search(r"\$\{[A-Za-z_]+(,,|\^\^)\}", text), name
