"""Baseline wrapper tool for the `book_reservation` primitive.

Part of `airline_skill`. Delegates to the frozen primitive tool `book_reservation`.
The optimizer may add guard/aggregation logic here; any helper it introduces
must be nested INSIDE the function below and prefixed with '_'.
"""


def book_reservation_wrapper(
    user_id: str,
    origin: str,
    destination: str,
    flight_type: str,
    cabin: str,
    flights: str,
    passengers: str,
    payment_methods: str,
    total_baggages: int,
    nonfree_baggages: int,
    insurance: str,
):
    """Create a flight reservation for a user with the specified travel details.

    Args:
        user_id (str): Identifier of the user making the reservation, such as
            'sara_doe_496'.
        origin (str): Departure airport IATA code, such as 'JFK'.
        destination (str): Arrival airport IATA code, such as 'LAX'.
        flight_type (str): Type of flight, either 'one_way' or 'round_trip'.
        cabin (str): Cabin class, one of 'basic_economy', 'economy', 'business'.
        flights (str): An array of objects containing the flight number and date for
            each segment.
        passengers (str): An array of objects containing first name, last name and date
            of birth for each passenger.
        payment_methods (str): An array of objects containing the payment id and amount
            for each payment method.
        total_baggages (int): The total number of baggage items included in the
            reservation.
        nonfree_baggages (int): The number of non-free baggage items included in the
            reservation.
        insurance (str): Whether travel insurance was purchased, either 'yes' or 'no'.

    Returns:
        The reservation confirmation details.
    """
    return book_reservation(
        user_id=user_id,
        origin=origin,
        destination=destination,
        flight_type=flight_type,
        cabin=cabin,
        flights=flights,
        passengers=passengers,
        payment_methods=payment_methods,
        total_baggages=total_baggages,
        nonfree_baggages=nonfree_baggages,
        insurance=insurance,
    )
