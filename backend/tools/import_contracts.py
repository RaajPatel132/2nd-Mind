"""Custom import-linter contract: modules talk only through their public interfaces.

Rule, for every import where importer and imported sit in *different* top-level modules
under ``root`` (for example ``secondmind.agent`` -> ``secondmind.providers``):

* ``root.<m>`` (the module's ``__init__``) may always be imported.
* ``root.<m>.<public>`` (for each name in ``public_subpackages``, e.g. ``adapters``) may be
  imported only by an importer that matches ``adapter_importers`` (composition roots and
  other adapters). Domain code never reaches another module's adapters.
* Anything deeper (``root.<m>.graph``, ``root.<m>.adapters.sql``...) is internal and forbidden.

Imports inside one top-level module are unrestricted.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

import grimp
from importlinter import Contract, ContractCheck, fields, output


@dataclass(frozen=True)
class _Violation:
    importer: str
    imported: str
    line_numbers: tuple[int, ...]
    reason: str


class PublicInterfaceContract(Contract):
    type_name = "public_interface"

    root = fields.StringField()
    public_subpackages = fields.ListField(subfield=fields.StringField(), required=False, default=[])
    adapter_importers = fields.ListField(subfield=fields.StringField(), required=False, default=[])

    def _matches_adapter_importer(self, module: str) -> bool:
        patterns: list[str] = self.adapter_importers  # type: ignore[assignment]
        for pattern in patterns:
            if fnmatch.fnmatchcase(module, pattern) or fnmatch.fnmatchcase(module, pattern + ".*"):
                return True
        return False

    def check(self, graph: grimp.ImportGraph, verbose: bool) -> ContractCheck:
        root: str = self.root  # type: ignore[assignment]
        public: set[str] = set(self.public_subpackages)  # type: ignore[arg-type]
        prefix = root + "."
        violations: list[_Violation] = []

        for importer in sorted(graph.modules):
            if not importer.startswith(prefix):
                continue
            importer_top = importer.split(".")[1]
            for imported in sorted(graph.find_modules_directly_imported_by(importer)):
                if not imported.startswith(prefix):
                    continue
                parts = imported.split(".")[1:]
                if parts[0] == importer_top:
                    continue
                reason: str | None = None
                if len(parts) == 1:
                    continue
                if len(parts) == 2 and parts[1] in public:
                    if self._matches_adapter_importer(importer):
                        continue
                    reason = (
                        f"only composition roots or adapters may import {prefix}{parts[0]}"
                        f".{parts[1]}"
                    )
                else:
                    reason = f"internal module; import from {prefix}{parts[0]} instead"
                details = graph.get_import_details(importer=importer, imported=imported)
                violations.append(
                    _Violation(
                        importer=importer,
                        imported=imported,
                        line_numbers=tuple(d["line_number"] for d in details),
                        reason=reason,
                    )
                )

        return ContractCheck(
            kept=not violations,
            metadata={"violations": violations},
        )

    def render_broken_contract(self, check: ContractCheck) -> None:
        violations: list[_Violation] = check.metadata["violations"]
        for v in violations:
            lines = ", ".join(f"l.{n}" for n in v.line_numbers) or "?"
            output.print_error(f"{v.importer} -> {v.imported} ({lines})", bold=True)
            output.print_error(f"    {v.reason}", bold=False)
            output.new_line()
