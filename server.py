import os
import re
import uuid
from datetime import datetime
from typing import Any

import psycopg
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("flight-booking")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres@localhost:5432/flight_booking",
)


def get_connection():
    return psycopg.connect(DATABASE_URL)


def is_valid_email(email: str) -> bool:
    pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    return bool(re.fullmatch(pattern, email.strip()))


@mcp.tool()
def search_flights(origin: str, destination: str, date: str) -> list[dict[str, Any]]:
    """
    Search flights by origin, destination, and date.
    Example:
    search_flights(origin="Kochi", destination="Bangalore", date="2026-11-20")
    """
    if not origin or not destination or not date:
        return [{"error": "origin, destination, and date are required"}]

    try:
        query_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return [{"error": "date must be in YYYY-MM-DD format"}]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    flight_number,
                    origin,
                    destination,
                    departs_at,
                    seats_left,
                    status,
                    delay_minutes
                FROM flights
                WHERE origin = %s
                  AND destination = %s
                  AND DATE(departs_at) = %s
                ORDER BY departs_at ASC;
                """,
                (origin.strip(), destination.strip(), query_date),
            )
            rows = cur.fetchall()

    if not rows:
        return [{"message": f"No flights found for {origin} to {destination} on {date}."}]

    flights: list[dict[str, Any]] = []
    for row in rows:
        (
            flight_id,
            flight_number,
            origin_name,
            destination_name,
            departs_at,
            seats_left,
            status,
            delay_minutes,
        ) = row
        flights.append(
            {
                "id": flight_id,
                "flight_number": flight_number,
                "origin": origin_name,
                "destination": destination_name,
                "departs_at": departs_at.isoformat(),
                "seats_left": seats_left,
                "status": status,
                "delay_minutes": delay_minutes,
            }
        )
    return flights

@mcp.tool()
def book_flight(
    flight_id: int,
    passenger_name: str,
    passenger_email: str,
    agent_name: str,
) -> dict[str, Any]:
    """
    Book a seat on an available flight and save which agent created the booking.
    Returns a booking reference if successful, or a clear error message.
    """
    if not passenger_name or not passenger_name.strip():
        return {"error": "Passenger name is required."}

    if not passenger_email or not is_valid_email(passenger_email):
        return {"error": "Invalid email address."}

    if not agent_name or not agent_name.strip():
        return {"error": "Agent name is required."}

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, seats_left, status
                    FROM flights
                    WHERE id = %s
                    FOR UPDATE;
                    """,
                    (flight_id,),
                )
                flight = cur.fetchone()

                if flight is None:
                    return {"error": "Flight not found."}

                existing_id, seats_left, status = flight
                if seats_left <= 0:
                    return {"error": "Sold out."}

                if status in {"cancelled", "departed", "landed"}:
                    return {"error": "This flight is not accepting new bookings."}

                cur.execute(
                    """
                    UPDATE flights
                    SET seats_left = seats_left - 1
                    WHERE id = %s
                      AND seats_left > 0
                    RETURNING seats_left;
                    """,
                    (flight_id,),
                )
                updated = cur.fetchone()
                if updated is None:
                    return {"error": "Sold out."}

                reference = f"BK-{uuid.uuid4().hex[:10].upper()}"
                cur.execute(
                    """
                    INSERT INTO bookings (
                        reference,
                        flight_id,
                        passenger_name,
                        passenger_email,
                        agent_name
                    )
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (
                        reference,
                        flight_id,
                        passenger_name.strip(),
                        passenger_email.strip(),
                        agent_name.strip(),
                    ),
                )

                return {
                    "booking_reference": reference,
                    "flight_id": flight_id,
                    "passenger_name": passenger_name.strip(),
                    "passenger_email": passenger_email.strip(),
                    "agent_name": agent_name.strip(),
                    "message": "Booking created successfully.",
                }


@mcp.tool()
def get_flight_status(
    flight_number: str | None = None,
    booking_reference: str | None = None,
) -> dict[str, Any]:
    """
    Get a flight's status and delay by flight number or booking reference.
    """
    if not flight_number and not booking_reference:
        return {"error": "Provide either flight_number or booking_reference."}

    if flight_number and booking_reference:
        return {"error": "Provide either flight_number or booking_reference, not both."}

    with get_connection() as conn:
        with conn.cursor() as cur:
            if flight_number:
                cur.execute(
                    """
                    SELECT flight_number, status, delay_minutes
                    FROM flights
                    WHERE flight_number = %s;
                    """,
                    (flight_number.strip(),),
                )
                flight = cur.fetchone()
                if flight is None:
                    return {"error": f"Flight {flight_number.strip()} not found."}
            else:
                cur.execute(
                    """
                    SELECT f.flight_number, f.status, f.delay_minutes
                    FROM bookings b
                    JOIN flights f ON f.id = b.flight_id
                    WHERE b.reference = %s;
                    """,
                    (booking_reference.strip(),),
                )
                flight = cur.fetchone()
                if flight is None:
                    return {"error": f"Booking {booking_reference.strip()} not found."}

    flight_number, status, delay_minutes = flight
    return {
        "flight_number": flight_number,
        "status": status,
        "delay_minutes": delay_minutes,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
