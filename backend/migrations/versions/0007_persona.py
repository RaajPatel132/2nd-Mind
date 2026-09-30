"""The sample persona and its copies (S4.10, S4.11, ADR-0038).

``workspaces.kind`` gains ``template`` (the canonical persona, owned by a system user and never
entered) and ``scratch`` (a guest's empty memory); a workspace records the seed it came from, its
version and how many days it was moved. ``copy_persona_template`` is a ``SECURITY DEFINER``
function: it copies a template's rows into a new workspace with new ids, every timestamp moved
forward by whole days and no model call, and it accepts nothing but a template as its source.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_GROUP_ROLE = "secondmind_rw"
KIND_BEFORE = "kind IN ('private', 'guest', 'persona_copy')"
KIND_AFTER = "kind IN ('private', 'guest', 'persona_copy', 'template', 'scratch')"

# A time-ordered UUID (version 7) for a moment: turn ids sort by time, and the history is paged by
# id, so a copy's past turns must keep their order.
UUID_V7_AT = """
CREATE FUNCTION uuid_v7_at(ts timestamptz) RETURNS uuid
LANGUAGE sql VOLATILE SET search_path = public, pg_temp AS $$
    WITH r AS (SELECT uuid_send(gen_random_uuid()) AS b)
    SELECT encode(
        set_byte(
            overlay(r.b PLACING substring(int8send(floor(extract(epoch FROM ts) * 1000)::bigint) FROM 3)
                    FROM 1 FOR 6),
            6, (get_byte(r.b, 6) & 15) | 112),
        'hex')::uuid
    FROM r
$$
"""  # noqa: E501 - SQL

COPY_FUNCTION = """
CREATE FUNCTION copy_persona_template(
    p_template uuid, p_workspace uuid, p_user uuid, p_shift_days integer
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_shift interval := make_interval(days => p_shift_days);
    -- A routine keeps its weekday: its clocks move by whole weeks.
    v_weeks interval := make_interval(days => (p_shift_days / 7) * 7);
    v_self_old uuid;
    v_self_new uuid;
BEGIN
    IF p_shift_days < 0 THEN
        RAISE EXCEPTION 'a copy moves forward in time' USING ERRCODE = '22023';
    END IF;
    -- The one rule that matters: the source is a template, never an ordinary workspace.
    IF NOT EXISTS (SELECT 1 FROM workspaces WHERE id = p_template AND kind = 'template') THEN
        RAISE EXCEPTION 'the source is not a persona template' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM workspaces
        WHERE id = p_workspace AND owner_user_id = p_user AND kind IN ('persona_copy')
    ) THEN
        RAISE EXCEPTION 'the target is not a new persona copy of this user' USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM memory_items WHERE workspace_id = p_workspace)
       OR EXISTS (SELECT 1 FROM turns WHERE workspace_id = p_workspace) THEN
        RAISE EXCEPTION 'the target workspace is not empty' USING ERRCODE = '22023';
    END IF;

    SELECT id INTO v_self_old FROM entities WHERE workspace_id = p_template AND kind = 'self';
    SELECT id INTO v_self_new FROM entities WHERE workspace_id = p_workspace AND kind = 'self';

    CREATE TEMP TABLE _persona_map (kind text NOT NULL, old_id uuid NOT NULL, new_id uuid NOT NULL,
                                    PRIMARY KEY (kind, old_id)) ON COMMIT DROP;
    INSERT INTO _persona_map SELECT 'turn', id, uuid_v7_at(started_at + v_shift)
        FROM turns WHERE workspace_id = p_template;
    INSERT INTO _persona_map SELECT 'entity', id, gen_random_uuid()
        FROM entities WHERE workspace_id = p_template AND kind <> 'self';
    INSERT INTO _persona_map VALUES ('entity', v_self_old, v_self_new);
    INSERT INTO _persona_map SELECT 'category', id, gen_random_uuid()
        FROM categories WHERE workspace_id = p_template;
    INSERT INTO _persona_map SELECT 'item', id, gen_random_uuid()
        FROM memory_items WHERE workspace_id = p_template;

    -- Vocabulary and categories (names only).
    INSERT INTO vocab_terms (id, workspace_id, vocab, slug, aliases, created_at)
        SELECT gen_random_uuid(), p_workspace, vocab, slug, aliases, now()
        FROM vocab_terms WHERE workspace_id = p_template;
    INSERT INTO categories (id, workspace_id, slug, display_name, aliases, created_at)
        SELECT m.new_id, p_workspace, c.slug, c.display_name, c.aliases, now()
        FROM categories c JOIN _persona_map m ON m.kind = 'category' AND m.old_id = c.id
        WHERE c.workspace_id = p_template;

    -- Turns: the past conversation and the turn that loaded the seed, moved with everything else.
    INSERT INTO turns (id, workspace_id, user_id, input, output, status, started_at, finished_at,
                       config_hash, prompt_versions, models, input_tokens, cached_input_tokens,
                       output_tokens, cost_usd, trace_status, error_code, error_message,
                       event_count, kind, parent_turn_id, charged_tokens)
        SELECT m.new_id, p_workspace, p_user, t.input, t.output, t.status,
               t.started_at + v_shift, t.finished_at + v_shift, t.config_hash, t.prompt_versions,
               t.models, t.input_tokens, t.cached_input_tokens, t.output_tokens, t.cost_usd,
               t.trace_status, t.error_code, t.error_message, 0, t.kind,
               (SELECT pm.new_id FROM _persona_map pm
                 WHERE pm.kind = 'turn' AND pm.old_id = t.parent_turn_id),
               t.charged_tokens
        FROM turns t JOIN _persona_map m ON m.kind = 'turn' AND m.old_id = t.id
        WHERE t.workspace_id = p_template
        ORDER BY t.started_at;

    -- Entities. The copy's own self entity takes the template's description of "me".
    UPDATE entities e SET aliases = s.aliases, labels = s.labels, attributes = s.attributes,
                          summary = s.summary, updated_at = now()
        FROM entities s WHERE s.id = v_self_old AND e.id = v_self_new;
    INSERT INTO entities (id, workspace_id, kind, name, aliases, labels, is_key, attributes,
                          summary, summary_updated_at, status, created_at, updated_at,
                          created_by_turn_id, updated_by_turn_id)
        SELECT m.new_id, p_workspace, e.kind, e.name, e.aliases, e.labels, e.is_key, e.attributes,
               e.summary, e.summary_updated_at + v_shift, e.status, now(), now(),
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = e.created_by_turn_id),
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = e.updated_by_turn_id)
        FROM entities e JOIN _persona_map m ON m.kind = 'entity' AND m.old_id = e.id
        WHERE e.workspace_id = p_template AND e.kind <> 'self';

    -- Memories. A routine's clocks move by whole weeks; everything else by the whole days.
    INSERT INTO memory_items (id, workspace_id, created_at, updated_at, created_by_turn_id,
            updated_by_turn_id, source, trust, raw_content, content_ref, status, kind, subtype,
            format, state, text, title, summary, category_id, tags, attributes, enrichment,
            rationale, subject_entity_id, predicate, value, mentioned_at, occurred_start,
            occurred_end, time_precision, rrule, due_at, valid_from, valid_to, modality, confidence,
            sentiment, rating, sensitivity, importance, access_count, last_accessed_at, in_core,
            core_confirmed_at, in_quick, quick_reason, quick_until)
        SELECT m.new_id, p_workspace, now(), now(),
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = i.created_by_turn_id),
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = i.updated_by_turn_id),
               i.source, i.trust, i.raw_content, i.content_ref, i.status, i.kind, i.subtype,
               i.format, i.state, i.text, i.title, i.summary,
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'category' AND pm.old_id = i.category_id),
               i.tags, i.attributes, i.enrichment, i.rationale,
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'entity' AND pm.old_id = i.subject_entity_id),
               i.predicate, i.value,
               i.mentioned_at + (CASE WHEN i.rrule IS NULL THEN v_shift ELSE v_weeks END),
               i.occurred_start + (CASE WHEN i.rrule IS NULL THEN v_shift ELSE v_weeks END),
               i.occurred_end + (CASE WHEN i.rrule IS NULL THEN v_shift ELSE v_weeks END),
               i.time_precision, i.rrule,
               i.due_at + (CASE WHEN i.rrule IS NULL THEN v_shift ELSE v_weeks END),
               i.valid_from + (CASE WHEN i.rrule IS NULL THEN v_shift ELSE v_weeks END),
               i.valid_to + (CASE WHEN i.rrule IS NULL THEN v_shift ELSE v_weeks END),
               i.modality, i.confidence, i.sentiment, i.rating, i.sensitivity, i.importance,
               0, NULL, i.in_core,
               i.core_confirmed_at + v_shift, i.in_quick, i.quick_reason,
               i.quick_until + (CASE WHEN i.rrule IS NULL THEN v_shift ELSE v_weeks END)
        FROM memory_items i JOIN _persona_map m ON m.kind = 'item' AND m.old_id = i.id
        WHERE i.workspace_id = p_template;

    INSERT INTO memory_entities (id, workspace_id, item_id, entity_id, role, created_by_turn_id,
                                 created_at)
        SELECT gen_random_uuid(), p_workspace, mi.new_id, me.new_id, x.role,
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = x.created_by_turn_id),
               now()
        FROM memory_entities x
        JOIN _persona_map mi ON mi.kind = 'item' AND mi.old_id = x.item_id
        JOIN _persona_map me ON me.kind = 'entity' AND me.old_id = x.entity_id
        WHERE x.workspace_id = p_template;

    INSERT INTO memory_links (id, workspace_id, src_item_id, link_type, dst_item_id,
                              created_by_turn_id, created_at)
        SELECT gen_random_uuid(), p_workspace, ms.new_id, x.link_type, md.new_id,
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = x.created_by_turn_id),
               now()
        FROM memory_links x
        JOIN _persona_map ms ON ms.kind = 'item' AND ms.old_id = x.src_item_id
        JOIN _persona_map md ON md.kind = 'item' AND md.old_id = x.dst_item_id
        WHERE x.workspace_id = p_template;

    INSERT INTO entity_relations (id, workspace_id, src_entity_id, relation, dst_entity_id,
                                  valid_from, valid_to, evidence_item_id, created_by_turn_id,
                                  created_at)
        SELECT gen_random_uuid(), p_workspace, ms.new_id, x.relation, md.new_id,
               x.valid_from + v_shift, x.valid_to + v_shift,
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'item' AND pm.old_id = x.evidence_item_id),
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = x.created_by_turn_id),
               now()
        FROM entity_relations x
        JOIN _persona_map ms ON ms.kind = 'entity' AND ms.old_id = x.src_entity_id
        JOIN _persona_map md ON md.kind = 'entity' AND md.old_id = x.dst_entity_id
        WHERE x.workspace_id = p_template;

    -- Triggers: the person a trigger waits for is named inside its spec.
    INSERT INTO triggers (id, workspace_id, item_id, "on", spec, fires_at, state, expires_at,
                          created_at, updated_at, created_by_turn_id, updated_by_turn_id)
        SELECT gen_random_uuid(), p_workspace, mi.new_id, x."on",
               CASE WHEN x.spec ? 'entity_id' THEN jsonb_set(x.spec, '{entity_id}', to_jsonb(
                        (SELECT pm.new_id::text FROM _persona_map pm
                          WHERE pm.kind = 'entity' AND pm.old_id = (x.spec ->> 'entity_id')::uuid)))
                    ELSE x.spec END,
               x.fires_at + v_shift, x.state, x.expires_at + v_shift, now(), now(),
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = x.created_by_turn_id),
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = x.updated_by_turn_id)
        FROM triggers x JOIN _persona_map mi ON mi.kind = 'item' AND mi.old_id = x.item_id
        WHERE x.workspace_id = p_template;

    -- Search keys keep the template's embeddings: no model is called for a copy.
    INSERT INTO memory_keys (id, workspace_id, item_id, key_kind, text, content_hash, embedding,
                             embedding_model, created_at, updated_at, position)
        SELECT gen_random_uuid(), p_workspace, mi.new_id, x.key_kind, x.text, x.content_hash,
               x.embedding, x.embedding_model, now(), now(), x.position
        FROM memory_keys x JOIN _persona_map mi ON mi.kind = 'item' AND mi.old_id = x.item_id
        WHERE x.workspace_id = p_template;

    INSERT INTO link_sources (id, workspace_id, item_id, turn_id, url, canonical_url, kind,
            fetch_status, fetch_reason, site, author, published_at, description, word_count,
            chunk_count, final_host, status_code, content_type, bytes_read, redirects,
            extraction_method, channel, duration_s, thumbnail_url, fetched_at, detail, created_at,
            updated_at)
        SELECT gen_random_uuid(), p_workspace, mi.new_id,
               (SELECT pm.new_id FROM _persona_map pm WHERE pm.kind = 'turn' AND pm.old_id = x.turn_id),
               x.url, x.canonical_url, x.kind, x.fetch_status, x.fetch_reason, x.site, x.author,
               x.published_at + v_shift, x.description, x.word_count, x.chunk_count, x.final_host,
               x.status_code, x.content_type, x.bytes_read, x.redirects, x.extraction_method,
               x.channel, x.duration_s, x.thumbnail_url, x.fetched_at + v_shift, x.detail, now(), now()
        FROM link_sources x JOIN _persona_map mi ON mi.kind = 'item' AND mi.old_id = x.item_id
        WHERE x.workspace_id = p_template;

    INSERT INTO conversation_keys (id, workspace_id, turn_id, role, seq, said_at, text,
                                   content_hash, embedding, embedding_model, created_at)
        SELECT gen_random_uuid(), p_workspace, mt.new_id, x.role, x.seq, x.said_at + v_shift,
               x.text, x.content_hash, x.embedding, x.embedding_model, now()
        FROM conversation_keys x JOIN _persona_map mt ON mt.kind = 'turn' AND mt.old_id = x.turn_id
        WHERE x.workspace_id = p_template;

    UPDATE workspaces w SET seed_id = t.seed_id, seed_version = t.seed_version,
                            moved_days = p_shift_days
        FROM workspaces t WHERE t.id = p_template AND w.id = p_workspace;
END $$
"""  # noqa: E501 - SQL


def upgrade() -> None:
    op.execute("ALTER TABLE workspaces DROP CONSTRAINT IF EXISTS ck_workspaces_ck_workspaces_kind")
    op.create_check_constraint(op.f("ck_workspaces_ck_workspaces_kind"), "workspaces", KIND_AFTER)
    op.add_column("workspaces", sa.Column("seed_id", sa.Text(), nullable=True))
    op.add_column("workspaces", sa.Column("seed_version", sa.Integer(), nullable=True))
    op.add_column("workspaces", sa.Column("seed_hash", sa.Text(), nullable=True))
    op.add_column("workspaces", sa.Column("moved_days", sa.Integer(), nullable=True))
    # One persona copy per person, and one template per seed.
    op.create_index(
        "uq_workspaces_owner_persona",
        "workspaces",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'persona_copy'"),
    )
    op.create_index(
        "uq_workspaces_template_seed",
        "workspaces",
        ["seed_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'template'"),
    )
    op.execute(UUID_V7_AT)
    op.execute(COPY_FUNCTION)
    for fn in (
        "copy_persona_template(uuid, uuid, uuid, integer)",
        "uuid_v7_at(timestamptz)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION copy_persona_template(uuid, uuid, uuid, integer) "
        f"TO {APP_GROUP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS copy_persona_template(uuid, uuid, uuid, integer)")
    op.execute("DROP FUNCTION IF EXISTS uuid_v7_at(timestamptz)")
    op.drop_index("uq_workspaces_template_seed", table_name="workspaces")
    op.drop_index("uq_workspaces_owner_persona", table_name="workspaces")
    op.drop_column("workspaces", "moved_days")
    op.drop_column("workspaces", "seed_hash")
    op.drop_column("workspaces", "seed_version")
    op.drop_column("workspaces", "seed_id")
    op.execute("DELETE FROM workspaces WHERE kind IN ('template', 'scratch')")
    op.execute("ALTER TABLE workspaces DROP CONSTRAINT IF EXISTS ck_workspaces_ck_workspaces_kind")
    op.create_check_constraint(op.f("ck_workspaces_ck_workspaces_kind"), "workspaces", KIND_BEFORE)
