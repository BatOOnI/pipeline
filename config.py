PROVIDER = "lmstudio"  # "lmstudio" or "openai"
MODE_CONTROL = "AUTO"  # AUTO | FORCE_CREATE | FORCE_PATCH

# LM Studio
LMSTUDIO_URL = "http://127.0.0.1:1235/v1/chat/completions"
LMSTUDIO_MODEL = "openai/gpt-oss-20b"
LMSTUDIO_API_KEY = ""
LMSTUDIO_TIMEOUT = 120

# OpenAI
OPENAI_API_KEY = ""
OPENAI_MODEL = "gpt-5.4-nano"
OPENAI_RESCUE_ENABLED = True
RESCUE_MODE = "OFF"  # OFF | ON | ASK_BEFORE_RESCUE

# Permission flow
PERMISSION_MODE = "workspace-write"  # read-only | workspace-write | danger-full-access | prompt | allow
PERMISSION_ALLOW_RULES = ""  # e.g. "run_cmd:python*" or "read_file"
PERMISSION_DENY_RULES = ""  # e.g. "run_cmd:git reset"
PERMISSION_ASK_RULES = ""  # e.g. "run_cmd:*"

# Runtime
MAX_ITERATIONS = 10
PROJECT_ROOT = "TEST"  # base container; create mode makes TEST-N inside it when possible
ACTIVE_PROJECT_ROOT = ""
SESSION_FILE = ".agent/session.json"
RUN_TIMEOUT = 15
MODEL_TIMEOUT = 120
LOG_FILE = "pipeline_log.txt"
RAW_LOG_FILE = ".agent/raw_pipeline_log.txt"
LOG_LEVEL = "COMPACT"  # COMPACT | VERBOSE | RAW
AUTO_RUN_COMMANDS = True
AUTO_VERIFY_PYTHON = True
AUTO_SMOKE_RUN = False
AUTO_GIT_CHECKPOINTS = True
ALLOW_LOCAL_MODEL_SWITCH = False

# Prompt / context control
PROMPT_CHAR_LIMIT = 12000
PATCH_FILES = ""
PATCH_SNIPPET_LINES = 80
MAX_OUTPUT_TOKENS = 2000
PROMPT_HISTORY_ITEMS = 6
PROMPT_HISTORY_CHARS = 2600
TEST_DIR_PREFIX = "TEST-"

# Guards
MAX_PARSE_ERRORS = 3
ALLOW_EMPTY_DONE_RETRY = True
REPEAT_ACTION_LIMIT = 2
STALL_TRIGGER = 2
PATCH_WRITE_MIN_RATIO = 0.55

# Git defaults
DEFAULT_REMOTE_URL = "https://github.com/BatOOnI/pipeline"
DEFAULT_COMMIT_MESSAGE = "update"
DEFAULT_TAG = "v1.0-working"
