"""Exact P0 compatibility projection; never a whole-file exclusion."""
STATUS_LINE=b'    UNKNOWN = "UNKNOWN"\n'
HOOK_LINE=b'    "src/football_system/domain/match.py": (\'    UNKNOWN = "UNKNOWN"\\n\',),\n'


def project_p0(relative,raw):
    needle={"src/football_system/domain/match.py":STATUS_LINE,
        "src/football_system/infrastructure/files/real_bridge_frozen.py":HOOK_LINE}.get(relative)
    if needle is not None:
        assert raw.count(needle)==1,("P0_EXACT_PROJECTION_REQUIRED",relative)
        return raw.replace(needle,b"",1)
    return raw
