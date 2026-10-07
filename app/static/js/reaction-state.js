// PR 313: the pure part of reacting, shared by paragraph reactions and comment votes
// (paragraph-menu.js) - what a click does to the counts, and the optimistic
// show-now / confirm / roll-back cycle around the request. Kept apart from the DOM so
// tests/js/reaction_state_harness.mjs can run it under Node.
(() => {
  // A paragraph takes one emoji per reader (app/db/reactions.py's toggle_reaction()):
  // picking the current one removes it, picking another moves it there.
  function toggleEmoji(counts, mine, emoji) {
    const next = { ...(counts || {}) };
    if (mine) {
      next[mine] = (next[mine] || 0) - 1;
      if (next[mine] <= 0) delete next[mine];
    }
    if (mine === emoji) return { counts: next, mine: null };
    next[emoji] = (next[emoji] || 0) + 1;
    return { counts: next, mine: emoji };
  }

  // A comment takes one vote per reader, like (1) or dislike (-1), with the same rule
  // (app/db/comment_reactions.py's toggle_comment_reaction()).
  function toggleVote(counts, mine, value) {
    const key = (v) => (v === 1 ? "like" : "dislike");
    const next = { like: counts?.like || 0, dislike: counts?.dislike || 0 };
    if (mine === 1 || mine === -1) next[key(mine)] = Math.max(0, next[key(mine)] - 1);
    if (mine === value) return { counts: next, mine: null };
    next[key(value)] += 1;
    return { counts: next, mine: value };
  }

  // Renders `guess` at once (pending = true), then whatever the server answers; if the
  // request fails or is refused, renders `previous` again. `request` resolves to the
  // server's {counts, mine} or null. Resolves to whether the server accepted it.
  async function optimistic({ previous, guess, render, request }) {
    render(guess, true);
    let result;
    try {
      result = await request();
    } catch {
      result = null;
    }
    if (result) {
      render(result, false);
      return true;
    }
    render(previous, false);
    return false;
  }

  window.reactionState = { toggleEmoji, toggleVote, optimistic };
})();
