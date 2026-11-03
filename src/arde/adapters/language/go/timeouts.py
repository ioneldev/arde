"""How long each child process may run before it is stopped. A hung child must not hang a run."""

GOINSPECT_TIMEOUT_S = 120
GO_BUILD_TIMEOUT_S = 300
GO_VET_TIMEOUT_S = 300
GO_TEST_TIMEOUT_S = 600
# Go's own clock, for one test binary. It must run out first, because only Go can name the test
# that hung: past this it prints the running tests and every goroutine, and fails the package.
GO_TEST_BINARY_TIMEOUT_S = 540
# Listing the files the build leaves out reads package metadata only.
GO_LIST_TIMEOUT_S = 120
# Reading three settings from the user's go env file.
GO_ENV_TIMEOUT_S = 30
# Laying out one source file.
GOFMT_TIMEOUT_S = 60
# A profile's extra command can be anything, so it gets as long as the tests do.
EXTRA_TEST_COMMAND_TIMEOUT_S = GO_TEST_TIMEOUT_S
