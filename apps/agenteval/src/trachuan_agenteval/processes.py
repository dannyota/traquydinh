from collections.abc import Mapping


def child_environment(source: Mapping[str, str], kind: str = "build") -> dict[str, str]:
    allowed = {"PATH"}
    if kind == "build":
        allowed |= {"HOME", "TMPDIR", "GOCACHE", "GOMODCACHE"}
    elif kind == "postgres":
        allowed |= {"PGPASSWORD", "PGPASSFILE"}
    elif kind != "system":
        raise ValueError("unknown child process kind")
    env = {key: source[key] for key in allowed if key in source}
    if kind == "build":
        env["GOPROXY"] = "off"
    return env
