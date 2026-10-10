"""Exact reversible release-only edits; never omit a file from byte comparison."""

V130_IDENTITY = '''
RELEASED_V130_IDENTITY = dict(schema_version="DAILY_OPERATOR_SOFTWARE_IDENTITY_V1", software="football-system",
    software_version="1.3.0", implementation_revision="package:45ce420e3eeffded6282e6a5390fe5487994125722004940d83f4f59c9f2cc9c",
    release_base_commit="a6952a4f9b86e1c273aa66e9f48c7930d8687a0a", execution_profile="OPENFOOTBALL_PIN_RELEASE_V1",
    migration_head="8ea7bcd49510")
RELEASED_V130_OPERATOR_HASH = "8132d15b954cd9966584fcf6f94540daa6ee9268e8991eee6e7d58a54430fe2d"
'''
V130_PROFILE = '''    if record.get("software_identity") == RELEASED_V130_IDENTITY:
        common = {"schema_version", "mode", "installation_id", "operator_code_hash", "runtime", "backups",
            "databases", "created_at_utc", "software_identity"}
        daily.require(set(record) == common and record["schema_version"] == daily.INSTALL_V2
            and record["operator_code_hash"] == RELEASED_V130_OPERATOR_HASH, "RELEASED_V130_IDENTITY_REQUIRED")
        return dict(previous_release=True, heads={daily.RELEASE_HEAD})
'''
V130_BYTE_PROFILE = '''
# Exact released wheel 2578bc97... has these LF lines among otherwise CRLF code.
# This is a fixture byte profile, not an alternative accepted runtime identity.
V130_BYTE_PROFILE = {
    "src/football_system/__init__.py": ((3,), "f9f0b7519b0f0d48b93f7c4a21186032eb3aa79f8d893f4a99f1f470fc0e313f"),
    "src/football_system/infrastructure/database/session.py": ((74,), "228fc5919398f89be563bd3f9fd7a620b7540da033a858839c0eca82a1782b61"),
    "src/football_system/infrastructure/files/real_bridge_frozen.py": ((47, 48, 49), "d875fa4c3f975619047d008d3cee098a01dc621d5b89578f9fdab9760825ce35"),
}
'''
V130_BYTE_RECONSTRUCTION = '''    if version == "1.3.0":
        for name, (lf_lines, expected_hash) in V130_BYTE_PROFILE.items():
            path = old / name
            value = b"".join(line.replace(b"\\r\\n", b"\\n") if i in lf_lines else line
                for i, line in enumerate(path.read_bytes().splitlines(keepends=True), 1))
            assert hashlib.sha256(value).hexdigest() == expected_hash
            path.write_bytes(value)
'''
VERSION_COUNTS = {"pyproject.toml": 1, "src/football_system/__init__.py": 1,
    "src/football_system/infrastructure/database/session.py": 1,
    "src/football_system/infrastructure/files/real_bridge_frozen.py": 3}
PAIRS = {
    "scripts/daily_operator.py": (
        ('RELEASE_VERSION = "1.3.1"', 'RELEASE_VERSION = "1.3.0"'),
        ('APPROVED_IMPLEMENTATION = "9429bcf695527d5c1d2663b2915faa388ab4a065"',
         'APPROVED_IMPLEMENTATION = "a6952a4f9b86e1c273aa66e9f48c7930d8687a0a"')),
    "scripts/wheel_e2e.py": (
        ('EXPECTED_VERSION = "1.3.1"', 'EXPECTED_VERSION = "1.3.0"'),
        ('V131_OPERATOR_INSTALLED_RELEASE_ACCEPTANCE_PASS', 'V130_OPERATOR_INSTALLED_RELEASE_ACCEPTANCE_PASS')),
    "scripts/operator_release_upgrade.py": (
        (V130_IDENTITY, ''), (V130_PROFILE, ''),
        ('V131_RELEASE_MAINTENANCE', 'V130_RELEASE_MAINTENANCE'),
        ('"release131-"', '"release13-"'), ('".v131-new"', '".v130-new"'),
        ('" TO 1.3.1"', '" TO 1.3.0"'), ('"release-v1.3.1"', '"release-v1.3.0"'),
        ('V131_RELEASE_REBIND', 'V130_RELEASE_REBIND')),
    "scripts/operator_release_acceptance.py": (
        ('import hashlib\n', ''), (V130_BYTE_PROFILE, ''), (V130_BYTE_RECONSTRUCTION, ''),
        ('source content, operator hashes and software identity are not substituted.',
         'no old source bytes, operator hashes or software identity are substituted.'),
        ('Installed v1.3.1 maintenance over archived v1.1/v1.2/v1.3 source fixtures',
         'Installed v1.3 maintenance over archived v1.1/v1.2 source fixtures'),
        ('"1.2.0": "8b7cfb3ac2e416a5c3c8f5046150245ab076f490",\n        "1.3.0": "0f677e834d5e2b6eb092ecaf86e4c70a60e38e98"}',
         '"1.2.0": "8b7cfb3ac2e416a5c3c8f5046150245ab076f490"}'),
        ('database_state, RELEASED_V120_IDENTITY, RELEASED_V130_IDENTITY', 'database_state, RELEASED_V120_IDENTITY'),
        ('''        assert legacy["software_identity"] == {"1.2.0": RELEASED_V120_IDENTITY, "1.3.0": RELEASED_V130_IDENTITY}[previous_version]
    old_journal = op.root / "release-v1.3.0"
    if previous_version == "1.3.0":
        old_journal.mkdir()
        (old_journal / "receipt.json").write_bytes(b"SYNTHETIC_PREVIOUS_RELEASE_HISTORY\\n")
''', '        assert legacy["software_identity"] == RELEASED_V120_IDENTITY\n'),
        ('" TO 1.3.1"', '" TO 1.3.0"'),
        ('if previous_version in {"1.1.0", "1.3.0"}:', 'if previous_version == "1.1.0":'),
        ('''    if previous_version == "1.3.0":
        assert (old_journal / "receipt.json").read_bytes() == b"SYNTHETIC_PREVIOUS_RELEASE_HISTORY\\n"
        assert result["databases_before"] == result["databases_after"]
''', ''),
        ('previous_source_version=previous_version,version="1.3.1"', 'previous_source_version=previous_version,version="1.3.0"'),
        ('for version in ("1.1.0", "1.2.0", "1.3.0")', 'for version in ("1.1.0", "1.2.0")'),
        ('status="PASS",version="1.3.1",upgrades=results', 'status="PASS",version="1.3.0",upgrades=results'),
        ('V131_OPERATOR_INSTALLED_RELEASE_ACCEPTANCE_PASS', 'V130_OPERATOR_INSTALLED_RELEASE_ACCEPTANCE_PASS')),
}


def project_release131(relative, raw):
    if relative in VERSION_COUNTS:
        assert raw.count(b"1.3.1") == VERSION_COUNTS[relative], relative
        raw = raw.replace(b"1.3.1", b"1.3.0")
    for new, old in PAIRS.get(relative, ()):
        assert raw.count(new.encode()) == 1, (relative, new)
        raw = raw.replace(new.encode(), old.encode(), 1)
    return raw
