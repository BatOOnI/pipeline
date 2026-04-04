PROVIDER = "lmstudio"  # "lmstudio" or "openai"

# LM Studio
LMSTUDIO_URL = "http://127.0.0.1:1235/v1/chat/completions"
LMSTUDIO_MODEL = "openai/gpt-oss-20b"
LMSTUDIO_TIMEOUT = 120

# OpenAI
OPENAI_API_KEY = ""
OPENAI_MODEL = "gpt-5.4-nano"

# Runtime
MAX_ITERATIONS = 10
PROJECT_ROOT = "TEST"
RUN_TIMEOUT = 15
LOG_FILE = "pipeline_log.txt"
AUTO_RUN_COMMANDS = True

# Git defaults
DEFAULT_REMOTE_URL = "https://github.com/BatOOnI/pipeline"
DEFAULT_COMMIT_MESSAGE = "update"
DEFAULT_TAG = "v1.0-working"
