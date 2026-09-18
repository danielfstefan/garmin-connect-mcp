"""Weight management tools for Garmin Connect MCP server."""

from typing import Annotated

from fastmcp import Context

from ..client import GarminAPIError
from ..response_builder import ResponseBuilder
from ..time_utils import parse_date_string

VALID_WEIGHT_UNITS = {"kg", "lbs"}


def _normalise_weight_unit(unit: str | None) -> str:
    """Normalise the user-supplied unit to 'kg' or 'lbs' (default 'kg')."""
    if unit is None:
        return "kg"
    unit = unit.strip().lower()
    if unit not in VALID_WEIGHT_UNITS:
        raise ValueError(f"unit must be one of {sorted(VALID_WEIGHT_UNITS)}, got: {unit!r}")
    return unit


def _local_iso_for_date(date_str: str) -> str:
    """Convert a YYYY-MM-DD date into an ISO timestamp anchored inside that calendar day.

    The Garmin API interprets ``dateTimestamp`` as *local* time and then re-buckets the
    entry using the Garmin ACCOUNT timezone, which is not necessarily the host timezone.
    Anchoring at local *midnight* is therefore unsafe: on a UTC host a midnight anchor is
    re-bucketed into the previous day whenever the account timezone sits behind UTC. That
    is the real-world off-by-one that files a 07:27 Helsinki weigh-in under the previous
    calendar day. Anchoring at local NOON keeps the entry inside the requested calendar
    day for any account timezone within +/-12h of the host, so the requested day survives
    the round trip.
    """
    parsed = parse_date_string(date_str)
    return parsed.strftime("%Y-%m-%dT12:00:00")


def _resolve_weigh_ins_by_ids(
    client, date_str: str, ids: list[int]
) -> tuple[list[dict], list[int]]:
    """Fetch the daily weigh-ins for ``date_str`` and match them against the requested ids.

    Returns:
        (matched_rows, missing_ids) where matched_rows are the full weigh-in rows whose
        ``samplePk`` matches one of the requested ids, and missing_ids lists the ids that
        had no matching entry on that date.
    """
    daily = client.safe_call("get_daily_weigh_ins", date_str)
    rows = daily.get("dateWeightList", []) if isinstance(daily, dict) else []

    wanted = set(str(i) for i in ids)
    matched = [row for row in rows if str(row.get("samplePk")) in wanted]
    found = {str(row.get("samplePk")) for row in matched}
    missing = [i for i in ids if str(i) not in found]
    return matched, missing


async def query_weight_data(
    date: Annotated[str | None, "Specific date ('today', 'yesterday', or YYYY-MM-DD)"] = None,
    start_date: Annotated[str | None, "Range start date (YYYY-MM-DD)"] = None,
    end_date: Annotated[str | None, "Range end date (YYYY-MM-DD)"] = None,
    ctx: Context | None = None,
) -> str:
    """
    Query weight data.

    Get weight measurements for a specific date or date range.
    """
    assert ctx is not None
    try:
        client = await ctx.get_state("client")

        # Determine query type
        if date:
            parsed_date = parse_date_string(date)
            date_str = parsed_date.strftime("%Y-%m-%d")
            weight_data = client.safe_call("get_daily_weigh_ins", date_str)
            return ResponseBuilder.build_response(
                data={"weigh_ins": weight_data, "date": date_str},
                metadata={"query_type": "single_date", "date": date_str},
            )
        elif start_date and end_date:
            weight_data = client.safe_call("get_weigh_ins", start_date, end_date)
            return ResponseBuilder.build_response(
                data={"weigh_ins": weight_data},
                metadata={"query_type": "range", "start_date": start_date, "end_date": end_date},
            )
        else:
            # Default to today
            date_str = parse_date_string("today").strftime("%Y-%m-%d")
            weight_data = client.safe_call("get_daily_weigh_ins", date_str)
            return ResponseBuilder.build_response(
                data={"weigh_ins": weight_data, "date": date_str},
                metadata={"query_type": "single_date", "date": date_str},
            )

    except GarminAPIError as e:
        return ResponseBuilder.build_error_response(
            e.message, "api_error", ["Check your Garmin Connect credentials"]
        )
    except Exception as e:
        return ResponseBuilder.build_error_response(str(e), "internal_error")


async def manage_weight_data(
    action: Annotated[str, "Action: 'add' or 'delete'"],
    weight: Annotated[float | None, "Weight value (for add action)"] = None,
    date: Annotated[str | None, "Date for entry (YYYY-MM-DD, defaults to today)"] = None,
    weigh_in_ids: Annotated[
        str | None, "Comma-separated samplePk IDs to delete (for delete action)"
    ] = None,
    unit: Annotated[str | None, "Weight unit: 'kg' or 'lbs' (for add action, default 'kg')"] = None,
    delete_all: Annotated[
        bool, "Delete ALL weigh-ins on the date (for delete action by date)"
    ] = False,
    local_timestamp: Annotated[
        str | None,
        "Explicit local ISO timestamp (YYYY-MM-DDTHH:MM:SS) so the entry lands on a chosen calendar day (for add action)",
    ] = None,
    gmt_timestamp: Annotated[
        str | None,
        "Explicit GMT/UTC ISO timestamp (YYYY-MM-DDTHH:MM:SS) for the entry (for add action)",
    ] = None,
    ctx: Context | None = None,
) -> str:
    """
    Add or delete weight (weigh-in) entries.

    Actions:
    - add: Add a new weight entry. Provide ``weight`` (required). ``date`` (YYYY-MM-DD)
      defaults to today. Optionally pass ``unit`` ('kg' or 'lbs') and/or explicit ISO
      ``local_timestamp`` / ``gmt_timestamp`` to control exactly which calendar day the
      entry lands on. When only a date is given the entry is anchored to local NOON of
      that date (noon rather than midnight, because Garmin re-buckets entries using the
      ACCOUNT timezone, so a midnight anchor can slip into the previous day).
    - delete: Delete weigh-in entries. Two modes:
        * By ID — provide ``weigh_in_ids`` (comma-separated samplePk ids) plus ``date``
          (the date those entries were recorded, defaults to today). Only the requested
          entries are removed (precise ``delete_weigh_in`` per entry).
        * By date — provide ``date`` only. The single weigh-in on that day is removed;
          if the day holds multiple entries you must pass ``delete_all=True`` to remove
          them all, otherwise nothing is deleted and the caller is asked to be specific.

    Note: the Garmin API returns stored weights in grams (e.g. 84690.0 == 84.69 kg). Read
    queries handle the conversion on the client side; the add path accepts ``weight`` in
    the chosen ``unit`` (kg by default) as documented here.
    """
    assert ctx is not None
    try:
        client = await ctx.get_state("client")

        if action == "add":
            if weight is None:
                return ResponseBuilder.build_error_response(
                    "Weight value required for add action",
                    "invalid_parameters",
                    ["Provide weight in the chosen unit (default kg)", "Example: weight=75.5"],
                )

            unit_key = _normalise_weight_unit(unit)
            date_str = (
                parse_date_string(date).strftime("%Y-%m-%d")
                if date
                else parse_date_string("today").strftime("%Y-%m-%d")
            )

            # Explicit timestamps give the caller full control over which calendar day the
            # entry lands on.
            if local_timestamp or gmt_timestamp:
                result = client.safe_call(
                    "add_weigh_in_with_timestamps",
                    weight,
                    unit_key,
                    local_timestamp or _local_iso_for_date(date_str),
                    gmt_timestamp or "",
                )
            else:
                # Anchored inside the chosen calendar day (local noon) so the day is
                # preserved through Garmin's account-timezone re-bucketing.
                result = client.safe_call(
                    "add_weigh_in_with_timestamps",
                    weight,
                    unit_key,
                    _local_iso_for_date(date_str),
                    "",
                )

            return ResponseBuilder.build_response(
                data={
                    "result": result,
                    "weight": weight,
                    "unit": unit_key,
                    "date": date_str,
                },
                analysis={"insights": [f"Added weight entry: {weight} {unit_key} on {date_str}"]},
                metadata={"action": "add", "unit": unit_key, "date": date_str},
            )

        elif action == "delete":
            date_str = (
                parse_date_string(date).strftime("%Y-%m-%d")
                if date
                else parse_date_string("today").strftime("%Y-%m-%d")
            )

            # Precise single/multi entry deletion by samplePk id.
            if weigh_in_ids:
                try:
                    ids = [
                        int(id_str.strip()) for id_str in weigh_in_ids.split(",") if id_str.strip()
                    ]
                except ValueError as e:
                    return ResponseBuilder.build_error_response(
                        f"Invalid weigh_in_ids: {e}",
                        "invalid_parameters",
                        [
                            "Provide comma-separated numeric samplePk IDs",
                            "Example: weigh_in_ids='123,456'",
                        ],
                    )

                if not ids:
                    return ResponseBuilder.build_error_response(
                        "No weigh-in IDs provided to delete",
                        "invalid_parameters",
                        ["Provide comma-separated samplePk IDs", "Example: weigh_in_ids='123,456'"],
                    )

                matched, missing = _resolve_weigh_ins_by_ids(client, date_str, ids)
                if not matched:
                    return ResponseBuilder.build_error_response(
                        f"No weigh-ins found on {date_str} matching any of the requested IDs: {ids}",
                        "not_found",
                        ["Check the date and samplePk IDs", f"Searched date: {date_str}"],
                    )

                for row in matched:
                    client.safe_call("delete_weigh_in", str(row.get("samplePk")), date_str)

                deleted_pks = [str(row.get("samplePk")) for row in matched]
                insights = [f"Deleted {len(matched)} weight entries on {date_str}"]
                if missing:
                    insights.append(f"IDs not found (skipped): {missing}")
                return ResponseBuilder.build_response(
                    data={
                        "result": {"deleted": deleted_pks, "missing_ids": missing},
                        "deleted_ids": deleted_pks,
                        "date": date_str,
                    },
                    analysis={"insights": insights},
                    metadata={"action": "delete", "mode": "by_id", "date": date_str},
                )

            # Date-based deletion.
            daily = client.safe_call("get_daily_weigh_ins", date_str)
            rows = daily.get("dateWeightList", []) if isinstance(daily, dict) else []

            if not rows:
                return ResponseBuilder.build_error_response(
                    f"No weigh-ins found on {date_str}",
                    "not_found",
                    ["Nothing to delete for this date", f"Date: {date_str}"],
                )

            if len(rows) > 1 and not delete_all:
                # Never nuke more entries than the caller asked for.
                return ResponseBuilder.build_error_response(
                    f"{len(rows)} weigh-ins found on {date_str}",
                    "multiple_entries",
                    [
                        "Pass weigh_in_ids (samplePk ids) to delete specific entries",
                        f"Available ids on {date_str}: {[r.get('samplePk') for r in rows]}",
                        "Or pass delete_all=True to remove all entries for the date",
                    ],
                )

            result = client.safe_call("delete_weigh_ins", date_str, delete_all)
            return ResponseBuilder.build_response(
                data={"result": result, "deleted_count": result, "date": date_str},
                analysis={
                    "insights": [
                        f"Deleted {result} weight "
                        f"{'entr' if result == 1 else 'entrie'}s on {date_str}"
                    ]
                },
                metadata={"action": "delete", "mode": "by_date", "date": date_str},
            )

        else:
            return ResponseBuilder.build_error_response(
                f"Invalid action: {action}",
                "invalid_parameters",
                ["Valid actions: 'add', 'delete'"],
            )

    except GarminAPIError as e:
        return ResponseBuilder.build_error_response(e.message, "api_error")
    except Exception as e:
        return ResponseBuilder.build_error_response(str(e), "internal_error")
