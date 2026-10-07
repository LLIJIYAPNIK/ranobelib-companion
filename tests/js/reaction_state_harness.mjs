// Runs app/static/js/reaction-state.js in a node:vm sandbox and prints what each
// scenario produced as JSON for tests/test_reaction_state_js.py.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");
const window = {};
vm.runInNewContext(source, { window });
const { toggleEmoji, toggleVote, optimistic } = window.reactionState;

// What optimistic() rendered, in order, and what it resolved to.
async function cycle(request) {
  const renders = [];
  const ok = await optimistic({
    previous: { counts: { "👍": 2 }, mine: null },
    guess: { counts: { "👍": 3 }, mine: "👍" },
    render: (state, pending) => renders.push({ state, pending }),
    request,
  });
  return { ok, renders };
}

const out = {
  emoji: {
    add: toggleEmoji({ "👍": 2 }, null, "👍"),
    addNew: toggleEmoji({ "👍": 2 }, null, "🔥"),
    remove: toggleEmoji({ "👍": 3 }, "👍", "👍"),
    removeLast: toggleEmoji({ "🔥": 1 }, "🔥", "🔥"),
    move: toggleEmoji({ "👍": 3, "🔥": 1 }, "🔥", "👍"),
    fromNothing: toggleEmoji(undefined, null, "❤️"),
  },
  vote: {
    like: toggleVote({ like: 1, dislike: 0 }, null, 1),
    unlike: toggleVote({ like: 2, dislike: 0 }, 1, 1),
    switch: toggleVote({ like: 2, dislike: 1 }, 1, -1),
    fromNothing: toggleVote(undefined, null, -1),
  },
  confirmed: await cycle(async () => ({ counts: { "👍": 5 }, mine: "👍" })),
  refused: await cycle(async () => null),
  thrown: await cycle(async () => {
    throw new Error("offline");
  }),
};

process.stdout.write(JSON.stringify(out));
