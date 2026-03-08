"""Claude tool schemas for the booking engine."""

BOOKING_TOOLS = [
    {
        "name": "check_availability",
        "description": "Check available appointment slots for a given date and service",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "service_name": {
                    "type": "string",
                    "description": "Name of the service requested (e.g. installation, repair, maintenance, emergency)",
                },
                "provider_name": {
                    "type": "string",
                    "description": "Preferred provider/technician name (optional)",
                },
            },
            "required": ["date", "service_name"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Book an appointment for the customer",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "time": {"type": "string", "description": "Start time in HH:MM (24-hour) format"},
                "service_name": {"type": "string", "description": "Name of the service to book"},
                "provider_name": {
                    "type": "string",
                    "description": "Provider/technician name (optional)",
                },
                "customer_name": {
                    "type": "string",
                    "description": "Customer's name for the appointment",
                },
                "customer_email": {
                    "type": "string",
                    "description": "Customer's email for calendar invite (optional)",
                },
                "notes": {
                    "type": "string",
                    "description": "Additional notes or description of the issue",
                },
            },
            "required": ["date", "time", "service_name"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": "Cancel an existing appointment",
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "UUID of the appointment to cancel",
                },
            },
            "required": ["appointment_id"],
        },
    },
    {
        "name": "reschedule_appointment",
        "description": "Reschedule an existing appointment to a new date and time",
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "UUID of the appointment to reschedule",
                },
                "new_date": {"type": "string", "description": "New date in YYYY-MM-DD format"},
                "new_time": {"type": "string", "description": "New start time in HH:MM (24-hour) format"},
            },
            "required": ["appointment_id", "new_date", "new_time"],
        },
    },
]
