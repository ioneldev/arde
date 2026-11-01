# 0005: Discovery is separate from explore

Status: approved 2026-11-01.

**Decision.** Discovery and explore are two agents. Discovery decides how to build, run and read a repo, and writes the profile. Explore works out what the service does, and builds the wiki.

**Why.** They do different jobs at different rates. Discovery runs once, and again only if the profile stops working. Explore runs on every real change, and keeps its input small because it does not carry discovery's instructions. Explore also starts after the inventory exists, so its schema can list real names.

**Cost.** Two prompts to maintain, and a handoff through the profile.
