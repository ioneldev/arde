"""How much of a command's output is kept. Past it, the end is kept or the run stops."""

from arde.adapters.child_process import OutputLimit

# goinspect prints one JSON document, and half of one is of no use, so a longer reply is refused.
GOINSPECT_REPLY_LIMIT = OutputLimit(max_bytes=64 * 1024 * 1024, when_longer="fail")

# A failing command says what went wrong at the end, and a reader needs no more than this.
CHECK_OUTPUT_LIMIT = OutputLimit(max_bytes=1024 * 1024, when_longer="keep_tail")
