"""How long each child process may run before it is stopped. A hung child must not hang a run."""

GOINSPECT_TIMEOUT_S = 120
GO_BUILD_TIMEOUT_S = 300
GO_VET_TIMEOUT_S = 300
GO_TEST_TIMEOUT_S = 600
# Listing the files the build leaves out reads package metadata only.
GO_LIST_TIMEOUT_S = 120
# A profile's extra command can be anything, so it gets as long as the tests do.
EXTRA_TEST_COMMAND_TIMEOUT_S = 600
