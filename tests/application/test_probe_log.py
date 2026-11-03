"""The probes an agent made, kept so a fact can cite one and code can copy what it returned."""

from arde.application.probe_log import ProbeLog
from arde.domain.facts import ProbeRecord


def a_probe(
    method: str = "GET", path_and_query: str = "/items?page=2", **changes: object
) -> ProbeRecord:
    fields: dict[str, object] = {
        "kind": "probe",
        "method": method,
        "path_and_query": path_and_query,
        "status": 200,
        "content_type": "application/json",
        "body": '{"items":[]}',
    }
    return ProbeRecord.model_validate({**fields, **changes})


def test_a_recorded_probe_is_found_by_its_method_and_path_and_query() -> None:
    log = ProbeLog()
    probe = a_probe()

    log.record(probe)

    assert log.find("GET", "/items?page=2") == probe


def test_a_probe_that_was_never_made_is_not_found() -> None:
    log = ProbeLog()
    log.record(a_probe())

    assert log.find("GET", "/items?page=3") is None
    assert log.find("HEAD", "/items?page=2") is None


def test_the_same_request_made_again_replaces_the_first_record() -> None:
    log = ProbeLog()
    log.record(a_probe(status=500, body="busy"))
    latest = a_probe(status=200)

    log.record(latest)

    assert log.find("GET", "/items?page=2") == latest


def test_two_methods_on_one_path_are_kept_apart() -> None:
    log = ProbeLog()
    get, head = a_probe("GET", "/items"), a_probe("HEAD", "/items", body="")

    log.record(get)
    log.record(head)

    assert (log.find("GET", "/items"), log.find("HEAD", "/items")) == (get, head)
