# Lessons from a working review panel

These rules came from using a personal panel across product, research and video work. Examples describe observed failures and fixes without reproducing anyone's cards or private records. The garden signs in this repository are invented demos.

## Ask for one decision

A card needs one question that a person can answer without unpacking another question first. The card gate was changed to refuse questions joined with “also” or “and” after cards began collecting separate choices. Split a design choice and a publication choice into separate reviews.

## Lead with the person's problem

The first sentences should say who is stuck and what becomes easier. A review item was dismissed as sounding like plumbing, so the rule became: explain the problem and the change before describing implementation. “Visitors miss the seed exchange” gives a reviewer a job; “new queue handler” gives them vocabulary homework.

## Keep options short and concrete

Use three words where possible. The live panel added a check for labels shorter than three words because bare approvals needed more context. “Use this sign” and “Change the words” make the intended action visible even when a reviewer scans quickly. This fork allows shorter labels but caps each at three words.

## Show the thing being judged

A description cannot stand in for a working screen or a clip. Product demo feedback asked for the frontend and results after terminal footage failed to show what a person would use. Put real screenshots or playable clips beside the question. For comparisons, let A and B occupy the same viewing area so the person can compare without shrinking both.

## Keep context beside the choice

A person should not need the agent's chat to understand a card. The panel began requiring visible result context even when evidence metadata was present. File references were useful to the agent but could leave the reviewer looking at a question without the result it referred to.

## Give numbers a practical meaning

A review number needs a reason, a checked source and an interpretation. The card gate now refuses a number that appears without those pieces. A duration only helps when the reviewer knows whether it makes the clip clearer, too slow or harder to finish. Put technical proof in supporting metadata and keep the visible explanation human.

## Preserve what the person answered

An answer belongs to the version shown at the time. The live panel binds answers to retained card versions because pairing a new answer with an old source can misrepresent the person's choice. This fork records the shown card and retains media snapshots. A card or media change while the page is open causes the save to stop and request another review.

## Append answers

Treat the answer log as history. The existing publisher refuses to overwrite answered card identities and asks for a new id when the work changes. This fork appends one row, flushes it to disk and makes a repeated save request return the original success. An accidental second tap must not become a second decision.

## Keep failed saves visible

Only advance when the server confirms the answer. The panel's recording outbox retains unsaved material after a failed write instead of pretending it reached storage. This fork applies the same principle to choices: a network failure leaves the selection and note on screen. It tells the person to keep the tab open and retry.

## Wait where the decision lives

Once a card exists, the agent reads its verdict rather than asking the same question in chat. Review decisions moved to the mobile panel after numbered chat choices and static review artifacts became competing places to answer. One queue gives both the person and the agent a shared place to look.

## Refuse a repeated card

A new id does not make the same request new work. The card gate rejects an exact repeated result, even if the publisher supplies another id. The practical reason is simple: a reviewer should not have to remember whether they already handled it. This fork rejects an exact repeated result, even when it has a different identity. An agent still needs to catch paraphrased duplicates.

## Check the served result

A valid file can still produce a broken card. The live card gate was extended to run the panel loader's own checks after an earlier green result failed to mean the card would appear. Test the page that the reviewer receives, including its media and saved answer. A file existing on disk proves only that the file exists.
