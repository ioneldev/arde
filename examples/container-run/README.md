# The one Docker command, run for real

`docker compose run --rm arde --repo https://github.com/mihai-iorga-emag/catalog-poc --branch main`, from a fresh `arde-home` volume, on 2026-11-04, on Docker Desktop's Linux (arm64). It cloned over HTTPS inside the container, ran every step on Linux, and committed `03e4561` in the volume. It succeeded in 479 s for $2.47 with no repair round. The result came out with `git bundle`, as the README says.

It is kept beside the deliverable run because its judge shows the part a perfect score doesn't: a mismatch, and what code made of it.

- **The mismatch.** These docs say of `qty` that "a leading '+' is accepted". That is true of `strconv.Atoi`, but in a query a literal `+` decodes to a space. So the judge predicted `GET /products/+1?qty=+2&currency=` would answer 200, and the service answered 400.
- **The re-test cleared it as noise.** A fresh judge, given the same docs and that one request, predicted 400. So the mismatch didn't block, and it stays in `trace.jsonl` as `judge_noise`.
- **The judge wrote it down as a clarity note.** The note says the docs mislead on `+`. The deliverable run's docs explain the decoding, and its judge got all 20 predictions right. Since `22e65b3`, a clarity note also reaches `report.json`'s warnings. This run predates that fix, so its report lists none.

`report.json` and `trace.jsonl` here are that run's, unchanged.
