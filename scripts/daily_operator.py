"""INPUT_PREPARATION only. Additive operator glue; frozen football-system is unchanged."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import sqlite3
import stat
import sys
import uuid

PROJECT = Path(__file__).resolve().parents[1]
MODE = "INPUT_PREPARATION"
BANNER = "MODE = INPUT_PREPARATION\nPRODUCTION DECISION = UNAVAILABLE\nREAL PERFORMANCE = INSUFFICIENT_PROSPECTIVE_SAMPLE"
HEAD = "6c859ab273fe"
# V1 is a released contract, not an alias for whichever migration is newest.
LEGACY_OPERATOR_CODE_HASH = "f0d9215911d7f83ff6d44720a4bebc320f0a9c301b3ce8b544984cc996941799"
LEGACY_RELEASE_COMMIT = "5ed940a8af8077be80549603a2da38aea77fc1bf"
CANDIDATE_HEAD = "7d96abc3840f"
INSTALL_V2 = "DAILY_OPERATOR_INSTALL_V2"
KINDS = {"SLATE", "FIXTURE", "SPORTTERY", "EVIDENCE"}
LIMIT = 4*1024*1024
FORBIDDEN = {"bundesliga_acceptance_20260911", "bundesliga_observed_training_draft", "bundesliga_probe_20260908"}
SUFFIXES = {".json", ".csv", ".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".html", ".raw"}
HINTS = {
    "INITIALIZATION_NOT_CONFIRMED": "未初始化。必须核对完整路径后输入确认语句。",
    "EXISTING_OR_PARTIAL_INSTALLATION_REQUIRES_REVIEW": "目录已存在或初始化未完成；请维护者核验，不能重建第二个库。",
    "INPUT_VALIDATION_FAILED_NO_IMPORT": "未导入。请核对原格式、来源文件SHA、分类和时间，修正资料包后再试。",
    "EXPIRED_DELETE_ONLY": "材料已到期，不读取或续期；请按原retention处理。",
    "IMPORT_FAILED_REVIEW_REQUIRED": "可能存在已提交的核心回执；保留现场，交维护者核对，不能换库重来。",
    "RECOVERY_REQUIRED_UNFINISHED_OPERATION": "发现未收尾操作；先核对核心回执和最新备份，停止新写入。",
    "DATABASE_MISSING_OR_REPLACED": "数据库缺失/被替换；检查已登记路径，禁止自动新建。",
    "OPERATOR_BUSY_OR_RECOVERY_REQUIRED": "另一个入口运行中或上次异常退出；请核对进程和回执，不自动清锁。",
    "ACTION_NOT_ALLOWED": "只允许菜单1至6以及0退出。",
    "OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED": "安装的软件身份/head与当前执行环境不兼容；保持停止，交维护者审核，不重标安装hash或降级数据库。",
}


class PreparationError(ValueError):
    pass


def explain(error):
    if isinstance(error, PreparationError):
        code = str(error)
        return code+": "+HINTS.get(code, "已停止；请核对安装身份、输入包与回执，不能绕过门禁。")
    return "INPUT_GATE_FAILED: 请核对包格式/路径/回执；未执行决策，不输出敏感异常内容。"


def require(value, code):
    if not value:
        raise PreparationError(code)


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    require(isinstance(value, str), "UTC_TIMESTAMP_REQUIRED")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "UTC_TIMESTAMP_REQUIRED")
    return result.astimezone(timezone.utc)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))+"\n").encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def operator_code_hash():
    digest = hashlib.sha256(b"DAILY_OPERATOR_GLUE_LF_V1\0")
    for name in ("daily.cmd", "scripts/daily_operator.py", "scripts/preparation_inputs.py", "src/football_system/infrastructure/files/real_bridge_frozen.py"):
        digest.update(name.encode()+b"\0"+(PROJECT / name).read_bytes().replace(b"\r\n", b"\n")+b"\0")
    return digest.hexdigest()


def candidate_software_identity():
    """Bind actual installed/source bytes without pretending to release a version."""
    import football_system
    from football_system.application.run_analysis import _code_revision

    require(football_system.__version__ == "1.1.0", "FROZEN_VERSION_REQUIRED")
    return dict(schema_version="DAILY_OPERATOR_SOFTWARE_IDENTITY_V1", software="football-system",
        software_version=football_system.__version__, implementation_revision=_code_revision(),
        release_base_commit=LEGACY_RELEASE_COMMIT, execution_profile="OPENFOOTBALL_CANDIDATE_V1",
        migration_head=CANDIDATE_HEAD)


def installation_profile(installation):
    """V1's missing identity fields mean the exact published V1 profile only.

    This read-only interpretation never rewrites an old installation manifest.
    V2 explicitly binds software/version/implementation/head plus the glue hash.
    """
    common = {"schema_version", "mode", "installation_id", "operator_code_hash", "runtime", "backups", "databases", "created_at_utc"}
    schema = installation.get("schema_version")
    if schema == "DAILY_OPERATOR_INSTALL_V1":
        require(set(installation) == common and installation.get("operator_code_hash") == LEGACY_OPERATOR_CODE_HASH,
            "OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED")
        return dict(legacy=True, migration_head=HEAD)
    require(schema == INSTALL_V2 and set(installation) == common | {"software_identity"},
        "OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED")
    require(installation.get("operator_code_hash") == operator_code_hash()
        and installation.get("software_identity") == candidate_software_identity(),
        "OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED")
    return dict(legacy=False, migration_head=CANDIDATE_HEAD)


def safe_path(path):
    """Reject links/reparse points/hardlinks and permanently closed roots before reads."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        name = item.name.casefold()
        require(name not in FORBIDDEN and not name.startswith("provider_capability_"), "CLOSED_CAPTURE_ROOT_FORBIDDEN")
        require(not name.startswith(".env") and name not in {"credentials", "credentials.json", "cookies", "cookies.txt"}, "SECRET_PATH_FORBIDDEN")
        if item.exists() or item.is_symlink():
            data = item.lstat()
            require(not stat.S_ISLNK(data.st_mode) and not getattr(data, "st_file_attributes", 0) & 0x400, "LINK_OR_REPARSE_FORBIDDEN")
            if item.is_file():
                require(data.st_nlink == 1, "HARDLINK_FORBIDDEN")
    return path


def relative(root, name):
    require(isinstance(name, str) and name and "\\" not in name and ":" not in name
            and not PurePosixPath(name).is_absolute() and all(p not in {"", ".", ".."} for p in name.split("/")), "CONTAINED_PATH_REQUIRED")
    root = safe_path(root)
    result = safe_path(root / name)
    require(result.is_relative_to(root), "CONTAINED_PATH_REQUIRED")
    return result


def read_bytes(path):
    path = safe_path(path)
    require(path.is_file() and 0 < path.stat().st_size <= LIMIT, "MISSING_OR_OVERSIZE_FILE")
    with path.open("rb") as stream:
        value = stream.read(LIMIT+1)
    require(0 < len(value) <= LIMIT, "MISSING_OR_OVERSIZE_FILE")
    return value


def read_json(path):
    from football_system.infrastructure.files.return_distribution import strict_return_json
    return strict_return_json(read_bytes(path))


def write_new(path, raw):
    require(0 < len(raw) <= LIMIT, "OPERATOR_OUTPUT_TOO_LARGE")
    safe_path(path)
    with Path(path).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def file_identity(path):
    value = safe_path(path).stat()
    return [value.st_dev, value.st_ino]


def verify_core():
    require(sys.version_info >= (3, 12), "PYTHON_312_PLUS_REQUIRED")
    import football_system
    from football_system.infrastructure.files.prospective import verify_prospective_configuration
    from football_system.infrastructure.files.return_distribution import verify_resource_configuration
    require(football_system.__version__ == "1.1.0", "FROZEN_VERSION_REQUIRED")
    require(Path(football_system.__file__).resolve() == (PROJECT / "src/football_system/__init__.py").resolve(), "FROZEN_SOURCE_IMPORT_REQUIRED")
    # Local-only Git check; no fetch/pull/install or credential access.
    from football_system.infrastructure.files.real_bridge_frozen import verify_frozen_checkout
    verify_frozen_checkout(PROJECT)
    policy = verify_prospective_configuration(PROJECT)
    returns, objective = verify_resource_configuration(PROJECT)
    require((returns.code_hash, returns.content_hash, objective.content_hash, policy.implementation_hash, policy.content_hash) == (
        "76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6",
        "bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47",
        "9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30",
        "6b2a73c14720c60eb30f9bc5d4158bccf095d397748cd1157eb53f13933ddb1f",
        "06f9cb5289959cf6387b324f9a8ef617d1960a68e88e23cb1423e0c15ef847f8"), "FROZEN_HASH_MISMATCH")


class Operator:
    def __init__(self, runtime, backups, *, synthetic=False, clock=None):
        from football_system.infrastructure.files.prospective import SystemProspectiveClock
        self.root, self.backups = safe_path(runtime), safe_path(backups)
        require(self.root != self.backups and not self.root.is_relative_to(self.backups)
                and not self.backups.is_relative_to(self.root), "RUNTIME_BACKUP_PATHS_OVERLAP")
        self.database_name = "synthetic.sqlite" if synthetic else "production.sqlite"
        self.classification = "SYNTHETIC" if synthetic else "REAL_SOURCE_DATA"
        self.clock = clock or SystemProspectiveClock()
        require(synthetic or self.clock.basis == "LOCAL_SYSTEM_UTC", "PRODUCTION_SYSTEM_CLOCK_REQUIRED")
        self.database = self.root / "db" / self.database_name
        self.operations = self.root / "ops" / ("s" if synthetic else "p")

    @property
    def confirmation(self):
        return "INIT "+str(self.root)

    def initialize(self, confirmation):
        require(confirmation == self.confirmation, "INITIALIZATION_NOT_CONFIRMED")
        require(not self.root.exists() and not self.backups.exists(), "EXISTING_OR_PARTIAL_INSTALLATION_REQUIRES_REVIEW")
        require(self.root.parent.parent.is_dir() and self.backups.parent.parent.is_dir(), "EXPECTED_PARENT_REQUIRED")
        software = candidate_software_identity()
        implementation = operator_code_hash()
        self.root.mkdir(parents=True, exist_ok=False)
        self.backups.mkdir(parents=True, exist_ok=False)
        for name in ("db", "inbox", "ops/p", "ops/s"):
            (self.root / name).mkdir(parents=True)
        from football_system.infrastructure.database.migrations import upgrade_database
        identities = {}
        ids = secrets.SystemRandom().sample(range(1, 2147483647), 2)
        for name, application_id in zip(("production.sqlite", "synthetic.sqlite"), ids, strict=True):
            path = self.root / "db" / name
            upgrade_database("sqlite:///"+path.as_posix(), PROJECT / "alembic.ini")
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(f"PRAGMA application_id={application_id}")
                require(connection.execute("SELECT version_num FROM alembic_version").fetchall() == [(software["migration_head"],)], "MIGRATION_HEAD_MISMATCH")
            identities[name] = dict(application_id=application_id, file_identity=file_identity(path))
        require(software == candidate_software_identity() and implementation == operator_code_hash(), "OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED")
        installation = dict(schema_version=INSTALL_V2, mode=MODE, installation_id=uuid.uuid4().hex,
            operator_code_hash=implementation, software_identity=software, runtime=str(self.root), backups=str(self.backups),
            databases=identities, created_at_utc=self.clock.now().isoformat())
        write_new(self.backups / "installation.json", encoded(installation))
        write_new(self.root / "operator-install.json", encoded(installation))  # Final marker only after both DBs exist.
        return installation

    def check(self):
        installation = read_json(self.root / "operator-install.json")
        require(installation["mode"] == MODE
                and installation["runtime"] == str(self.root) and installation["backups"] == str(self.backups), "INSTALLATION_PATH_MISMATCH")
        profile = installation_profile(installation)
        require(read_json(self.backups / "installation.json") == installation, "BACKUP_INSTALLATION_MISMATCH")
        require(set(installation["databases"]) == {"production.sqlite", "synthetic.sqlite"}
                and len({d["application_id"] for d in installation["databases"].values()}) == 2,
                "DATABASE_ROLES_MUST_BE_DISTINCT")
        for name, expected in installation["databases"].items():
            path = self.root / "db" / name
            require(path.is_file() and file_identity(path) == expected["file_identity"], "DATABASE_MISSING_OR_REPLACED")
            with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)) as conn:
                require(conn.execute("PRAGMA application_id").fetchone()[0] == expected["application_id"], "DATABASE_IDENTITY_MISMATCH")
                require(conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(profile["migration_head"],)],
                    "OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED" if profile["legacy"] else "MIGRATION_HEAD_MISMATCH")
        return installation

    def _require_current_installation(self, installation):
        require(not installation_profile(installation)["legacy"], "OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED")

    @contextmanager
    def guard(self, *, writing=False):
        installation = self.check()
        if writing:
            self._require_current_installation(installation)
        lock = safe_path(self.root / "operator.lock")
        try:
            write_new(lock, encoded(dict(pid=os.getpid(), at=self.clock.now().isoformat())))
        except FileExistsError:
            raise PreparationError("OPERATOR_BUSY_OR_RECOVERY_REQUIRED") from None
        try:
            installation = self.check()
            if writing:
                self._require_current_installation(installation)
            latest = [timestamp(self._intent(p)["recorded_at_utc"]) for p in self.operations.iterdir() if p.is_dir()]
            require(not latest or self.clock.now() >= max(latest), "OPERATOR_CLOCK_REGRESSION")
            if writing:
                require(not self._partial_backups(), "RECOVERY_REQUIRED_PARTIAL_BACKUP")
                for folder in sorted(self.operations.iterdir()):
                    safe_path(folder)
                    if folder.is_dir() and not (folder / "receipt.json").is_file():
                        raise PreparationError("RECOVERY_REQUIRED_UNFINISHED_OPERATION")
            yield
        finally:
            lock.unlink()

    def _engine(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        from football_system.infrastructure.database.session import configure_sqlite_engine
        # No parent mkdir and no SQLite default creation, even if the DB disappears.
        def connect():
            self._require_current_installation(self.check())
            return sqlite3.connect(self.database.as_uri()+"?mode=rw", uri=True, check_same_thread=False)
        return configure_sqlite_engine(create_engine("sqlite://", creator=connect, poolclass=NullPool))

    def _partial_backups(self):
        partial = []
        for path in self.backups.iterdir():
            safe_path(path)
            if path.is_dir() and not (path / "COMPLETE").is_file():
                partial.append(path.name)
        return sorted(partial)

    def _package(self, name, kind):
        require(kind in KINDS, "INPUT_KIND_NOT_ALLOWED")
        package = relative(self.root / "inbox", name)
        require(package.is_dir(), "INPUT_PACKAGE_REQUIRED")
        meta = read_json(package / "package.json")
        require(set(meta) == {"schema_version", "kind", "file", "rights_reference", "retention_until_utc",
            "durable_ledger_authorized", "local_backup_authorized"}, "PACKAGE_FIELDS_INVALID")
        require(meta["schema_version"] == "DAILY_INPUT_PACKAGE_V1" and meta["kind"] == kind
                and meta["durable_ledger_authorized"] is True and meta["local_backup_authorized"] is True
                and isinstance(meta["rights_reference"], str) and bool(meta["rights_reference"].strip()), "PACKAGE_AUTHORIZATION_REQUIRED")
        require(timestamp(meta["retention_until_utc"]) > self.clock.now(), "EXPIRED_DELETE_ONLY")
        relative(package, meta["file"])
        paths = []
        for path in package.rglob("*"):
            safe_path(path)
            paths.append(path)
            require(len(paths) <= 64, "PACKAGE_TOO_LARGE")
        payloads = {}
        for path in sorted(paths):
            safe_path(path)
            if path.is_file():
                require(timestamp(meta["retention_until_utc"]) > self.clock.now(), "EXPIRED_DELETE_ONLY")
                require(path.suffix.lower() in SUFFIXES and path.suffix.lower() != ".raw", "INPUT_FILE_TYPE_NOT_ALLOWED")
                payloads[path.relative_to(package).as_posix()] = read_bytes(path)
                require(sum(map(len, payloads.values())) <= 16*1024*1024, "PACKAGE_TOO_LARGE")
        require(meta["file"] in payloads and meta["file"] != "package.json" and sum(map(len, payloads.values())) <= 16*1024*1024, "PACKAGE_TOO_LARGE_OR_MISSING_ENTRY")
        return meta, payloads

    def _intent(self, folder):
        intent = read_json(folder / "intent.json")
        installation = read_json(self.root / "operator-install.json")
        require(intent["installation_id"] == installation["installation_id"] and intent["database"] == self.database_name
                and intent["data_classification"] == self.classification and intent["kind"] in KINDS, "INTENT_INSTALLATION_MISMATCH")
        digest = sha(encoded({key: intent[key] for key in ("kind", "database", "installation_id", "files")}))
        require(intent["operation_id"] == digest and folder.name == digest[:20], "INTENT_IDENTITY_MISMATCH")
        return intent

    def import_package(self, name, kind):
        require(kind in KINDS, "INPUT_KIND_NOT_ALLOWED")
        with self.guard(writing=True):
            # Live package retry validates identical bytes. Expired material can
            # only be viewed by its existing receipt ID, never re-imported.
            meta, payloads = self._package(name, kind)
            files = {name: dict(bytes=len(raw), sha256=sha(raw)) for name, raw in payloads.items()}
            installation_id = read_json(self.root / "operator-install.json")["installation_id"]
            operation_id = sha(encoded(dict(kind=kind, database=self.database_name, installation_id=installation_id, files=files)))
            folder = self.operations / operation_id[:20]
            if (folder / "receipt.json").is_file():
                require(read_json(folder / "receipt.json")["operation_id"] == operation_id, "OPERATION_DIRECTORY_COLLISION")
                self._validate()
                return read_json(folder / "receipt.json")
            from scripts.preparation_inputs import validate_input
            try:
                validate_input(kind, relative(self.root / "inbox", name), meta["file"], self.clock,
                    self.classification, timestamp(meta["retention_until_utc"]))
            except Exception:
                raise PreparationError("INPUT_VALIDATION_FAILED_NO_IMPORT") from None
            folder.mkdir(exist_ok=False)
            intent = dict(operation_id=operation_id, kind=kind, database=self.database_name, files=files,
                installation_id=installation_id, data_classification=self.classification,
                retention_until_utc=meta["retention_until_utc"], recorded_at_utc=self.clock.now().isoformat())
            write_new(folder / "intent.json", encoded(intent))
            payload = folder / "payload"
            payload.mkdir()
            for name, raw in payloads.items():
                destination = relative(payload, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                write_new(destination, raw)
            from football_system.infrastructure.database.session import create_session_factory
            from scripts.preparation_inputs import apply_input, json_value
            engine = self._engine()
            try:
                require(timestamp(meta["retention_until_utc"]) > self.clock.now(), "EXPIRED_DELETE_ONLY")
                result = apply_input(kind, payload, meta["file"], create_session_factory(engine), self.clock,
                    self.classification, PROJECT / "config/live.toml", timestamp(meta["retention_until_utc"]))
                result = json_value(result)
            except Exception as error:
                # Never claim a rollback across multiple filesystem/core transactions.
                write_new(folder / "failure.json", encoded(dict(code=type(error).__name__, status="RECOVERY_REQUIRED")))
                raise PreparationError("IMPORT_FAILED_REVIEW_REQUIRED") from None
            finally:
                engine.dispose()
            archive_files = {}
            for path in sorted(folder.rglob("*")):
                safe_path(path)
                if path.is_file() and path != folder / "intent.json":
                    raw = read_bytes(path)
                    archive_files[path.relative_to(folder).as_posix()] = dict(bytes=len(raw), sha256=sha(raw))
            receipt = dict(**intent, result=result, status=result["status"], payload_files=archive_files,
                input_only=True, production_decision="UNAVAILABLE", real_performance="INSUFFICIENT_PROSPECTIVE_SAMPLE")
            write_new(folder / "receipt.json", encoded(receipt))
            return receipt

    def receipt(self, operation_id):
        require(len(operation_id) == 64 and all(c in "0123456789abcdef" for c in operation_id), "OPERATION_ID_INVALID")
        with self.guard():
            folder = self.operations / operation_id[:20]
            self._intent(folder)
            value = read_json(folder / "receipt.json")
            require(value["operation_id"] == operation_id, "OPERATION_DIRECTORY_COLLISION")
            return value

    def missing(self):
        with self.guard():
            with closing(sqlite3.connect(self.database.as_uri()+"?mode=ro", uri=True)) as conn:
                counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
                    "matches", "sporttery_bonus_snapshots", "live_market_consensus_lineages", "pv_evidence")}
            receipts = [read_json(p / "receipt.json") for p in sorted(self.operations.iterdir()) if (p / "receipt.json").is_file()]
            pending = [p.name for p in self.operations.iterdir() if p.is_dir() and not (p / "receipt.json").is_file()]
            return dict(mode=MODE, database=self.database_name, counts_not_readiness=counts,
                last_input_receipt=max(receipts, key=lambda r: (timestamp(r["recorded_at_utc"]), r["operation_id"]))["operation_id"] if receipts else None,
                unfinished_operations=pending, partial_backups=self._partial_backups(), model_pin_status="NOT_VERIFIED", p_quant="MODEL_UNAVAILABLE",
                input_receipts=[dict(operation_id=r["operation_id"], kind=r["kind"], status=r["status"],
                    expired=timestamp(r["retention_until_utc"]) <= self.clock.now(),
                    issue_count=r["result"].get("artifact", {}).get("issue_count", 0)) for r in receipts],
                missing=["PRODUCTION_DECISION_ADAPTER_UNAVAILABLE", "PROVIDER_CREDENTIAL_AND_DATA_USE_NOT_VERIFIED",
                    "CURRENT_MARKET_COVERAGE_AND_FRESHNESS_NOT_VERIFIED", "MODEL_PIN_NOT_VERIFIED_OR_MODEL_UNAVAILABLE",
                    "REAL_EPOCH_BOOTSTRAP_NOT_ACTIVATED"], real_performance="INSUFFICIENT_PROSPECTIVE_SAMPLE")

    def validate(self):
        with self.guard():
            return self._validate()

    def _validate(self):
        with closing(sqlite3.connect(self.database.as_uri()+"?mode=ro", uri=True)) as conn:
            require(conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "DATABASE_INTEGRITY_FAILED")
            require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "DATABASE_FK_FAILED")
        expired, unfinished = [], []
        for folder in sorted(self.operations.iterdir()):
            if not folder.is_dir():
                continue
            intent = self._intent(folder)
            if not (folder / "receipt.json").is_file():
                unfinished.append(intent["operation_id"])
                continue
            receipt = read_json(folder / "receipt.json")
            require(all(receipt[k] == v for k, v in intent.items()), "INTENT_RECEIPT_MISMATCH")
            if timestamp(receipt["retention_until_utc"]) <= self.clock.now():
                expired.append(intent["operation_id"])
                continue  # Do not reopen expired source bytes.
            present = set()
            for path in folder.rglob("*"):
                safe_path(path)
                if path.is_file() and path not in {folder / "intent.json", folder / "receipt.json"}:
                    present.add(path.relative_to(folder).as_posix())
            require(present == set(receipt["payload_files"]), "OPERATION_FILE_SET_CHANGED")
            for name, info in receipt["payload_files"].items():
                raw = read_bytes(relative(folder, name))
                require(len(raw) == info["bytes"] and sha(raw) == info["sha256"], "INPUT_FILE_HASH_MISMATCH")
            for name, info in intent["files"].items():
                require(receipt["payload_files"].get("payload/"+name) == info, "STAGED_INPUT_MISMATCH")
        partial = self._partial_backups()
        return dict(status="RECOVERY_REQUIRED" if unfinished or partial else "FILES_CHECKED", expired_delete_only=expired,
            unfinished_operations=unfinished, partial_backups=partial, meaning="INPUT_CHECK_ONLY_NOT_PROSPECTIVE_AUDIT")

    def backup(self):
        with self.guard():
            checked = self._validate()
            installation = read_json(self.root / "operator-install.json")
            destination = self.backups / uuid.uuid4().hex[:12]
            destination.mkdir(exist_ok=False)
            manifest = dict(schema_version="DAILY_OPERATOR_BACKUP_V1", database=self.database_name,
                installation_id=read_json(self.root / "operator-install.json")["installation_id"],
                created_at_utc=self.clock.now().isoformat(), files={}, excluded_expired=[], retention_by_operation={}, status="COMPLETE",
                operator_state=checked["status"], unfinished_operations=checked["unfinished_operations"])
            if installation["schema_version"] == INSTALL_V2:
                manifest.update(schema_version="DAILY_OPERATOR_BACKUP_V2", software_identity=installation["software_identity"],
                    operator_code_hash=installation["operator_code_hash"], migration_head=installation["software_identity"]["migration_head"])
            snapshot = destination / self.database_name
            with closing(sqlite3.connect(self.database.as_uri()+"?mode=ro", uri=True)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                require(target.execute("PRAGMA integrity_check").fetchall() == [("ok",)] and not target.execute("PRAGMA foreign_key_check").fetchall(), "BACKUP_DATABASE_INVALID")
                manifest["database_application_id"] = target.execute("PRAGMA application_id").fetchone()[0]
                manifest["pv_receipt_watermark"] = target.execute("SELECT COALESCE(MAX(sequence),0) FROM pv_receipts").fetchone()[0]
            write_new(destination / "operator-install.json", read_bytes(self.root / "operator-install.json"))
            for folder in sorted(self.operations.iterdir()):
                if not folder.is_dir():
                    continue
                intent = self._intent(folder)
                manifest["retention_by_operation"][intent["operation_id"]] = intent["retention_until_utc"]
                expired = timestamp(intent["retention_until_utc"]) <= self.clock.now()
                if expired:
                    manifest["excluded_expired"].append(dict(operation_id=intent["operation_id"], retention_until_utc=intent["retention_until_utc"]))
                for path in sorted(folder.rglob("*")) if not expired else [folder / "intent.json", folder / "receipt.json", folder / "failure.json"]:
                    safe_path(path)
                    if path.is_file():
                        if not expired:
                            require(timestamp(intent["retention_until_utc"]) > self.clock.now(), "EXPIRED_DELETE_ONLY")
                        dest = destination / "ops" / path.relative_to(self.operations)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        write_new(dest, read_bytes(path))
                if not expired and (folder / "receipt.json").is_file():
                    for name, expected in read_json(folder / "receipt.json")["payload_files"].items():
                        copied = read_bytes(relative(destination / "ops" / folder.name, name))
                        require(len(copied) == expected["bytes"] and sha(copied) == expected["sha256"], "BACKUP_SOURCE_HASH_MISMATCH")
            for path in sorted(destination.rglob("*")):
                if path.is_file():
                    # Databases may exceed the input-file byte bound.
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    manifest["files"][path.relative_to(destination).as_posix()] = dict(bytes=path.stat().st_size, sha256=digest)
            write_new(destination / "backup-manifest.json", encoded(manifest))
            write_new(destination / "COMPLETE", b"INPUT_PREPARATION_BACKUP\n")
            return dict(path=str(destination), **manifest)


def main(argv=None, *, input_fn=input, output=print):
    argv = sys.argv[1:] if argv is None else argv
    output(BANNER)
    if argv:
        output("ARGUMENTS_NOT_ALLOWED: use the six-item menu; no overrides")
        return 2
    verify_core()
    operator = Operator(PROJECT.parent / "football_runtime/v1.1.0", PROJECT.parent / "football_backups/v1.1.0")
    if not (operator.root / "operator-install.json").exists():
        output("首次初始化仅准备数据；不会运行预测或发送HTTP。")
        output("新建安装类型："+INSTALL_V2+" / OPENFOOTBALL_CANDIDATE_V1 / head="+CANDIDATE_HEAD)
        output(str(operator.root)+"\n"+str(operator.backups))
        output("将创建独立production.sqlite与synthetic.sqlite；输入完整确认语句：\n"+operator.confirmation)
        operator.initialize(input_fn("> ").strip())
    while True:
        output(BANNER)
        output("1 今日准备\n2 导入竞彩SP\n3 导入赛前事实\n4 查看缺项\n5 数据/文件校验\n6 备份\n0 退出")
        choice = input_fn("> ").strip()
        if choice == "0":
            return 0
        try:
            if choice == "1":
                fixture = input_fn("可选FIXTURE包目录（inbox内相对名称，空则不导入身份）：").strip()
                if fixture:
                    output(json.dumps(operator.import_package(fixture, "FIXTURE"), ensure_ascii=False))
                value = operator.import_package(input_fn("SLATE包目录：").strip(), "SLATE")
            elif choice == "2":
                value = operator.import_package(input_fn("SPORTTERY包目录：").strip(), "SPORTTERY")
            elif choice == "3":
                value = operator.import_package(input_fn("EVIDENCE包目录：").strip(), "EVIDENCE")
            elif choice == "4":
                value = operator.missing()
            elif choice == "5":
                value = operator.validate()
            elif choice == "6":
                value = operator.backup()
            else:
                raise PreparationError("ACTION_NOT_ALLOWED")
            output(json.dumps(value, ensure_ascii=False, indent=2))
        except Exception as error:
            output(explain(error))


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT))
    sys.path.insert(0, str(PROJECT / "src"))
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(explain(error), file=sys.stderr)
        raise SystemExit(1) from None
