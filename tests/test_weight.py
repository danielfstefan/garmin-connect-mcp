"""Tests for the weight management tool (manage_weight_data)."""

import json

from freezegun import freeze_time

from garmin_connect_mcp.tools.weight import manage_weight_data


class _FakeClient:
    """Records safe_call invocations and returns canned results."""

    def __init__(self):
        self.calls = []
        self.daily_results = {}  # date -> {"dateWeightList": [...]}

    def safe_call(self, method_name, *args, **kwargs):
        self.calls.append((method_name, args, kwargs))
        if method_name == "get_daily_weigh_ins":
            return self.daily_results.get(args[0], {"dateWeightList": []})
        if method_name == "delete_weigh_ins":
            rows = self.daily_results.get(args[0], {"dateWeightList": []}).get("dateWeightList", [])
            return len(rows)
        # add / delete_weigh_in return a marker so we can assert they were invoked
        return "ok"


class _FakeCtx:
    def __init__(self, client):
        self._client = client

    async def get_state(self, name):
        assert name == "client"
        return self._client


def _parse(response: str) -> dict:
    return json.loads(response)


async def test_add_with_date_uses_with_timestamps_and_kg_default():
    client = _FakeClient()
    ctx = _FakeCtx(client)

    with freeze_time("2026-05-01"):
        resp = _parse(
            await manage_weight_data(action="add", weight=84.69, date="2026-05-02", ctx=ctx)
        )

    # Must route through add_weigh_in_with_timestamps with correct positional args:
    # (weight, unitKey='kg', dateTimestamp=<local ISO anchored to the chosen day>, gmtTimestamp='').
    assert client.calls, "expected at least one safe_call"
    name, args, kwargs = client.calls[-1]
    assert name == "add_weigh_in_with_timestamps"
    assert args[0] == 84.69
    assert args[1] == "kg"
    assert args[2].startswith("2026-05-02T")  # chosen calendar day preserved
    assert args[3] == ""
    # Backwards-compatible response shape
    assert resp["data"]["weight"] == 84.69
    assert resp["data"]["unit"] == "kg"
    assert resp["data"]["date"] == "2026-05-02"
    assert resp["metadata"]["action"] == "add"


async def test_add_defaults_date_to_today():
    client = _FakeClient()
    ctx = _FakeCtx(client)

    with freeze_time("2026-05-01"):
        resp = _parse(await manage_weight_data(action="add", weight=75.0, ctx=ctx))

    name, args, kwargs = client.calls[-1]
    assert name == "add_weigh_in_with_timestamps"
    assert args[2].startswith("2026-05-01T")  # defaults to today
    assert resp["data"]["date"] == "2026-05-01"


async def test_add_accepts_lbs_and_explicit_timestamps():
    client = _FakeClient()
    ctx = _FakeCtx(client)

    resp = _parse(
        await manage_weight_data(
            action="add",
            weight=186.0,
            unit="lbs",
            date="2026-05-02",
            local_timestamp="2026-05-02T08:30:00",
            gmt_timestamp="2026-05-02T07:30:00Z",
            ctx=ctx,
        )
    )

    name, args, kwargs = client.calls[-1]
    assert name == "add_weigh_in_with_timestamps"
    assert args[1] == "lbs"
    assert args[2] == "2026-05-02T08:30:00"  # explicit local passed through
    assert args[3] == "2026-05-02T07:30:00Z"  # explicit GMT passed through
    assert resp["data"]["unit"] == "lbs"


async def test_add_requires_weight():
    client = _FakeClient()
    ctx = _FakeCtx(client)

    resp = _parse(await manage_weight_data(action="add", ctx=ctx))
    assert resp["error"]["type"] == "invalid_parameters"
    assert not client.calls


async def test_add_rejects_invalid_unit():
    client = _FakeClient()
    ctx = _FakeCtx(client)

    resp = _parse(await manage_weight_data(action="add", weight=75.0, unit="stone", ctx=ctx))
    assert resp["error"]["type"] == "internal_error"
    assert "unit" in resp["error"]["message"].lower()


async def test_delete_by_ids_uses_single_delete_weigh_in():
    client = _FakeClient()
    client.daily_results["2026-05-02"] = {
        "dateWeightList": [
            {
                "samplePk": 111,
                "calendarDate": "2026-05-02",
                "timestampGMT": 0,
                "weight": 84690,
                "sourceType": "MANUAL",
            },
            {
                "samplePk": 222,
                "calendarDate": "2026-05-02",
                "timestampGMT": 0,
                "weight": 84700,
                "sourceType": "MANUAL",
            },
        ]
    }
    ctx = _FakeCtx(client)

    resp = _parse(
        await manage_weight_data(action="delete", date="2026-05-02", weigh_in_ids="111", ctx=ctx)
    )

    # Must call precise delete_weigh_in(samplePk, cdate), NOT delete_weigh_ins.
    deletes = [c for c in client.calls if c[0] == "delete_weigh_in"]
    assert deletes == [("delete_weigh_in", ("111", "2026-05-02"), {})]
    assert not [c for c in client.calls if c[0] == "delete_weigh_ins"]
    assert resp["data"]["deleted_ids"] == ["111"]
    assert resp["metadata"]["mode"] == "by_id"


async def test_delete_by_ids_handles_multiple_and_missing():
    client = _FakeClient()
    client.daily_results["2026-05-02"] = {
        "dateWeightList": [
            {"samplePk": 111, "weight": 84690},
            {"samplePk": 222, "weight": 84700},
        ]
    }
    ctx = _FakeCtx(client)

    resp = _parse(
        await manage_weight_data(
            action="delete", date="2026-05-02", weigh_in_ids="111,999", ctx=ctx
        )
    )

    deletes = [c[1][0] for c in client.calls if c[0] == "delete_weigh_in"]
    assert deletes == ["111"]  # only the matched id is deleted
    assert resp["data"]["result"]["missing_ids"] == [999]
    assert resp["data"]["deleted_ids"] == ["111"]


async def test_delete_by_date_single_entry_deletes():
    client = _FakeClient()
    client.daily_results["2026-05-02"] = {"dateWeightList": [{"samplePk": 111, "weight": 84690}]}
    ctx = _FakeCtx(client)

    resp = _parse(await manage_weight_data(action="delete", date="2026-05-02", ctx=ctx))

    deletes = [c for c in client.calls if c[0] == "delete_weigh_ins"]
    assert deletes == [("delete_weigh_ins", ("2026-05-02", False), {})]
    assert resp["data"]["deleted_count"] == 1


async def test_delete_by_date_multiple_refuses_without_delete_all():
    client = _FakeClient()
    client.daily_results["2026-05-02"] = {
        "dateWeightList": [
            {"samplePk": 111, "weight": 84690},
            {"samplePk": 222, "weight": 84700},
        ]
    }
    ctx = _FakeCtx(client)

    resp = _parse(await manage_weight_data(action="delete", date="2026-05-02", ctx=ctx))

    assert resp["error"]["type"] == "multiple_entries"
    # Never call delete_weigh_ins (or nuke anything) when ambiguous without delete_all
    assert not [c for c in client.calls if c[0] == "delete_weigh_ins"]
    assert not [c for c in client.calls if c[0] == "delete_weigh_in"]


async def test_delete_by_date_multiple_with_delete_all_deletes_all():
    client = _FakeClient()
    client.daily_results["2026-05-02"] = {
        "dateWeightList": [
            {"samplePk": 111, "weight": 84690},
            {"samplePk": 222, "weight": 84700},
        ]
    }
    ctx = _FakeCtx(client)

    resp = _parse(
        await manage_weight_data(action="delete", date="2026-05-02", delete_all=True, ctx=ctx)
    )

    deletes = [c for c in client.calls if c[0] == "delete_weigh_ins"]
    assert deletes == [("delete_weigh_ins", ("2026-05-02", True), {})]
    assert resp["data"]["deleted_count"] == 2
