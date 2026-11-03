"""The HTTP status an error code's status stands for, as Go source writes it."""

import re

# As net/http declares them in status.go. An integration test holds this to the toolchain's file.
NET_HTTP_STATUSES: dict[str, int] = {
    "StatusContinue": 100,
    "StatusSwitchingProtocols": 101,
    "StatusProcessing": 102,
    "StatusEarlyHints": 103,
    "StatusOK": 200,
    "StatusCreated": 201,
    "StatusAccepted": 202,
    "StatusNonAuthoritativeInfo": 203,
    "StatusNoContent": 204,
    "StatusResetContent": 205,
    "StatusPartialContent": 206,
    "StatusMultiStatus": 207,
    "StatusAlreadyReported": 208,
    "StatusIMUsed": 226,
    "StatusMultipleChoices": 300,
    "StatusMovedPermanently": 301,
    "StatusFound": 302,
    "StatusSeeOther": 303,
    "StatusNotModified": 304,
    "StatusUseProxy": 305,
    "StatusTemporaryRedirect": 307,
    "StatusPermanentRedirect": 308,
    "StatusBadRequest": 400,
    "StatusUnauthorized": 401,
    "StatusPaymentRequired": 402,
    "StatusForbidden": 403,
    "StatusNotFound": 404,
    "StatusMethodNotAllowed": 405,
    "StatusNotAcceptable": 406,
    "StatusProxyAuthRequired": 407,
    "StatusRequestTimeout": 408,
    "StatusConflict": 409,
    "StatusGone": 410,
    "StatusLengthRequired": 411,
    "StatusPreconditionFailed": 412,
    "StatusRequestEntityTooLarge": 413,
    "StatusRequestURITooLong": 414,
    "StatusUnsupportedMediaType": 415,
    "StatusRequestedRangeNotSatisfiable": 416,
    "StatusExpectationFailed": 417,
    "StatusTeapot": 418,
    "StatusMisdirectedRequest": 421,
    "StatusUnprocessableEntity": 422,
    "StatusLocked": 423,
    "StatusFailedDependency": 424,
    "StatusTooEarly": 425,
    "StatusUpgradeRequired": 426,
    "StatusPreconditionRequired": 428,
    "StatusTooManyRequests": 429,
    "StatusRequestHeaderFieldsTooLarge": 431,
    "StatusUnavailableForLegalReasons": 451,
    "StatusInternalServerError": 500,
    "StatusNotImplemented": 501,
    "StatusBadGateway": 502,
    "StatusServiceUnavailable": 503,
    "StatusGatewayTimeout": 504,
    "StatusHTTPVersionNotSupported": 505,
    "StatusVariantAlsoNegotiates": 506,
    "StatusInsufficientStorage": 507,
    "StatusLoopDetected": 508,
    "StatusNotExtended": 510,
    "StatusNetworkAuthenticationRequired": 511,
}
_NUMBER = re.compile(r"[1-5][0-9][0-9]")
# Matching is by text, so net/http imported under another name is not read.
_NET_HTTP_CONSTANT = re.compile(r"http\.(\w+)")


def http_status(written: str) -> int | None:
    """The status a number or a net/http constant stands for. None for anything else, such as a
    variable or a call, whose value only running the code shows."""
    text = written.strip()
    if _NUMBER.fullmatch(text):
        return int(text)
    constant = _NET_HTTP_CONSTANT.fullmatch(text)
    return NET_HTTP_STATUSES.get(constant.group(1)) if constant else None
