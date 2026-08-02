# Historical run-schema inventory fixtures

These records are deterministic, synthetic evidence for the approved Gate 2.1
historical-schema inventory. They were derived from the committed `models.py`
contract that introduced each declared schema and from the additive schema-6
bridges documented in `manifest.json`.

No fixture contains bytes copied from Continuo's private `runs/` directory, the
Jobs checkout, a live provider response, or another project. Paths, hashes,
commands, provider text, policy text, and repository metadata are invented and
use the neutral `/fixture/repo` and `example.invalid` namespaces.

## Fixture policy

- `schema_v1.json` through `schema_v6_base.json` represent the six declared
  historical contracts.
- `schema_v6_current.json` represents the fully populated current schema-6
  compatibility shape.
- Schema-6 supervisor, provenance, writer, and ownership variants are documented
  deterministic in-memory derivations, not invented schema versions.
- Invalid-version and invalid-envelope cases are also in-memory derivations so
  malformed bytes are never mistaken for ordinary run fixtures.
- `manifest.json` records exact fixture SHA-256 values, source commits, expected
  treatment/disposition, derivations, and the approved 27-row matrix.

Fixture checks are read-only. They do not import the orchestration runtime,
invoke Git or a provider, open a target repository or coordination database, or
read/write private run storage. Executable migration, load, save, rollback, and
runtime error-reporting tests belong to the next separately approved Gate 2
item.

Changing a fixture requires an intentional manifest checksum update and review.
The source fixture bytes are preserved exactly; derived cases operate on
in-memory copies only.
