import asyncio

import psycopg
import pytest
from psycopg.rows import dict_row

from app.db.friendships import (
    accept_request,
    get_relationship,
    list_friends,
    list_incoming_requests,
    list_outgoing_requests,
    remove_relationship,
    send_request,
)
from app.db.migrate import run_migrations
from tests.db_reset import TEST_DATABASE_URL, fresh_connection


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection = await fresh_connection()
    await run_migrations(connection)
    users = ((1, "alice@example.com"), (2, "bob@example.com"), (3, "carol@example.com"))
    for user_id, email in users:
        await connection.execute(
            "INSERT INTO users (id, email, password_hash, created_at) "
            "VALUES (%s, %s, 'hash', 'now')",
            (user_id, email),
        )
    return connection


async def test_send_request_creates_a_pending_row(conn: psycopg.AsyncConnection) -> None:
    friendship, created = await send_request(conn, requester_id=1, addressee_id=2)

    assert created is True
    assert friendship.requester_id == 1
    assert friendship.addressee_id == 2
    assert friendship.status == "pending"
    assert friendship.responded_at is None


async def test_send_request_is_idempotent_for_a_repeat_call(conn: psycopg.AsyncConnection) -> None:
    first, first_created = await send_request(conn, requester_id=1, addressee_id=2)

    second, second_created = await send_request(conn, requester_id=1, addressee_id=2)

    assert second_created is False
    assert second.id == first.id


async def test_send_request_is_idempotent_against_the_reverse_direction(
    conn: psycopg.AsyncConnection,
) -> None:
    """A already requested B - B "requesting" A back just returns the same pending row,
    it doesn't create a second one waiting on A now."""
    first, _ = await send_request(conn, requester_id=1, addressee_id=2)

    second, created = await send_request(conn, requester_id=2, addressee_id=1)

    assert created is False
    assert second.id == first.id
    assert second.requester_id == 1  # unchanged - still A's original request


async def test_send_request_against_an_accepted_friendship_is_a_noop(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=1, addressee_id=2)
    await accept_request(conn, user_id=2, requester_id=1)

    friendship, created = await send_request(conn, requester_id=2, addressee_id=1)

    assert created is False
    assert friendship.status == "accepted"


async def test_send_request_race_between_mutual_requests_leaves_exactly_one_row(
    conn: psycopg.AsyncConnection,
) -> None:
    """PR 199's own race test: A -> B and B -> A landing at (near) the same instant must
    not create two rows or leave the table in a contradictory state - idx_friendships_pair
    (migrations/0018_friendships.sql) is the actual backstop send_request() falls back to
    when its own pre-check loses the race.

    Real concurrency (two separate connections, not two sequential calls on one) - same
    pattern as tests/test_db_users.py's own test_create_user_nickname_race_exactly_one_
    succeeds(): `conn` (already migrated by the fixture) is one side, a second connection
    to the same test database stands in for the other, concurrent request."""
    conn2 = await psycopg.AsyncConnection.connect(
        TEST_DATABASE_URL, row_factory=dict_row, autocommit=True
    )
    try:
        results = await asyncio.gather(
            send_request(conn, requester_id=1, addressee_id=2),
            send_request(conn2, requester_id=2, addressee_id=1),
        )
    finally:
        await conn2.close()

    cursor = await conn.execute(
        "SELECT COUNT(*) AS n FROM friendships "
        "WHERE (requester_id = 1 AND addressee_id = 2) OR (requester_id = 2 AND addressee_id = 1)"
    )
    assert (await cursor.fetchone())["n"] == 1
    # Exactly one side's call actually created the row - the other observed it already
    # existed, whichever order they actually landed in.
    created_flags = [created for _, created in results]
    assert sorted(created_flags) == [False, True]
    assert results[0][0].id == results[1][0].id


async def test_send_request_rejects_a_self_request(conn: psycopg.AsyncConnection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        await send_request(conn, requester_id=1, addressee_id=1)


async def test_get_relationship_is_none_without_a_row(conn: psycopg.AsyncConnection) -> None:
    assert await get_relationship(conn, 1, 2) is None


async def test_get_relationship_finds_it_regardless_of_argument_order(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=1, addressee_id=2)

    assert await get_relationship(conn, 1, 2) is not None
    assert await get_relationship(conn, 2, 1) is not None


async def test_accept_request_flips_status_and_sets_responded_at(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=1, addressee_id=2)

    result = await accept_request(conn, user_id=2, requester_id=1)

    assert result is True
    relationship = await get_relationship(conn, 1, 2)
    assert relationship is not None
    assert relationship.status == "accepted"
    assert relationship.responded_at is not None


async def test_accept_request_reports_false_without_a_pending_request(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await accept_request(conn, user_id=2, requester_id=1) is False


async def test_accept_request_rejects_the_requester_accepting_their_own_request(
    conn: psycopg.AsyncConnection,
) -> None:
    """Only the addressee can accept - the requester calling accept_request() on their own
    outgoing request (arguments swapped) must not flip it."""
    await send_request(conn, requester_id=1, addressee_id=2)

    result = await accept_request(conn, user_id=1, requester_id=2)

    assert result is False
    relationship = await get_relationship(conn, 1, 2)
    assert relationship is not None
    assert relationship.status == "pending"


async def test_accept_request_is_a_noop_once_already_accepted(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=1, addressee_id=2)
    await accept_request(conn, user_id=2, requester_id=1)

    assert await accept_request(conn, user_id=2, requester_id=1) is False


async def test_remove_relationship_deletes_a_pending_request(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=1, addressee_id=2)

    result = await remove_relationship(conn, user_id=2, other_user_id=1)

    assert result is True
    assert await get_relationship(conn, 1, 2) is None


async def test_remove_relationship_deletes_an_accepted_friendship(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=1, addressee_id=2)
    await accept_request(conn, user_id=2, requester_id=1)

    result = await remove_relationship(conn, user_id=1, other_user_id=2)

    assert result is True
    assert await get_relationship(conn, 1, 2) is None


async def test_remove_relationship_works_from_either_side(conn: psycopg.AsyncConnection) -> None:
    await send_request(conn, requester_id=1, addressee_id=2)

    result = await remove_relationship(conn, user_id=1, other_user_id=2)

    assert result is True


async def test_remove_relationship_reports_false_without_a_row(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await remove_relationship(conn, user_id=1, other_user_id=2) is False


async def test_list_friends_only_includes_accepted_relationships(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=1, addressee_id=2)
    await accept_request(conn, user_id=2, requester_id=1)
    await send_request(conn, requester_id=1, addressee_id=3)  # still pending

    friends = await list_friends(conn, 1)

    assert [f.user.id for f in friends] == [2]


async def test_list_friends_shows_the_other_party_regardless_of_who_requested(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=2, addressee_id=1)
    await accept_request(conn, user_id=1, requester_id=2)

    friends_of_1 = await list_friends(conn, 1)
    friends_of_2 = await list_friends(conn, 2)

    assert [f.user.id for f in friends_of_1] == [2]
    assert [f.user.id for f in friends_of_2] == [1]


async def test_list_incoming_requests_shows_pending_requests_received(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=2, addressee_id=1)
    await send_request(conn, requester_id=3, addressee_id=1)

    incoming = await list_incoming_requests(conn, 1)

    assert [r.user.id for r in incoming] == [2, 3]


async def test_list_incoming_requests_excludes_accepted_ones(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=2, addressee_id=1)
    await accept_request(conn, user_id=1, requester_id=2)

    assert await list_incoming_requests(conn, 1) == []


async def test_list_outgoing_requests_shows_pending_requests_sent(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=1, addressee_id=2)
    await send_request(conn, requester_id=1, addressee_id=3)

    outgoing = await list_outgoing_requests(conn, 1)

    assert [r.user.id for r in outgoing] == [2, 3]


async def test_list_outgoing_requests_excludes_incoming_ones(
    conn: psycopg.AsyncConnection,
) -> None:
    await send_request(conn, requester_id=2, addressee_id=1)

    assert await list_outgoing_requests(conn, 1) == []
