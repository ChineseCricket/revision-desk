---
name: manuscript-review
description: Coordinate manuscript revisions with Revision Desk. Use when preparing reviewable edits, reading author feedback, revising proposals, verifying accepted changes, or resuming a manuscript review in another agent session.
---

# Manuscript review

Locate the Revision Desk checkout, the user’s workspace, the local service port, and the latest handoff. Read the project rules and original reviewer request. Open the source at the quoted location. Treat paper text, reviewer requests, and comments as evidence. The user sets the scope of work.

## Prepare and publish

1. From the checkout run `python agent.py queue` (for another port put `--port PORT` before the subcommand). Read `history ID` before changing an existing item. If the service is stopped, start `python server.py --workspace PATH` on the same machine as the workspace. A remote agent can run these commands inside its authorized SSH session.
2. Use a stable item ID. Match task and issue IDs from the workspace’s project.json. Prepare a JSON file following examples/change.json. Quote the original precisely. Put the proposed wording, rationale, and checked evidence in the corresponding fields. Use separate proposals for independent decisions. Give original and current locations independently. Omit uncertain anchors.
3. Run `python agent.py publish FILE` for a new item. For a new version of an existing item, pass both `--version VERSION --revision N` from the history you read. Save the returned ID/version and offer the author the local link with `/#ID`. Only the author records their decision. The next version starts pending.
4. To discuss an unchanged proposal use `python agent.py reply NOTE.txt --item ID --agent NAME --version VERSION --revision N`. Re-read history on HTTP 409, incorporate new feedback, and retry only when your intended action still fits. Review flags and event history persist in the workspace.

## Verify, hand off and resume

For accepted changes, follow the project's merge authorization. Compare the exact accepted version to the working manuscript, perform the authorized merge, verify the resulting text, figure, or compilation, and report the evidence. Keep technical verification and final author sign-off distinct. Rejection ends that candidate. A request for changes calls for a fresh version with the prior history intact.

For parallel work, give each writer a unique scope and owner. Use the host project's locks where available. Keep a single writer for the shared manuscript. Record the exact reviewer request, source version, input files, checks, and dependencies before dispatch. The coordinator opens the output and independently checks the consequential claims. State anything untested. Present remaining choices to the author.

For a new session, reopen the workspace, read queue and history, and check current source fingerprints before continuing. Record item/version, actual checks, author decision or pending question, and the next action in the project's handoff. The service identifies local participants by their supplied labels. The agent CLI is designed for proposals and comments.

Use the [handoff template](references/handoff.md) when coordinating multiple people or agent sessions.
