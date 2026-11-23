DO $$
DECLARE
    v_prompt_id         UUID := gen_random_uuid();
    v_prompt_version_id UUID := gen_random_uuid();
BEGIN
    INSERT INTO prompts (id, name)
    VALUES (v_prompt_id, 'booking appointment');

    INSERT INTO prompt_versions (id, prompt_id, version, content, input_schema, output_schema, strategy)
    VALUES (v_prompt_version_id, v_prompt_id, 1, 'First draft of the prompt...', '{}', '{}', 'lead_generation');

    INSERT INTO states (id, prompt_version_id, state_schema)
    VALUES (gen_random_uuid(), v_prompt_version_id, $json$
{
  "type": "object",
  "properties": {
    "appointment_date": {
      "anyOf": [
        {
          "type": "null"
        },
        {
          "type": "string",
          "format": "date"
        }
      ]
    },
    "appointment_start_at": {
      "anyOf": [
        {
          "type": "null"
        },
        {
          "type": "string",
          "format": "date-time"
        }
      ]
    },
   
    
    "booking_id": {
      "anyOf": [
        {
          "type": "null"
        },
        {
          "type": "string",
          "format": "uuid"
        }
      ]
    },
    "customer_email": {
      "anyOf": [
        {
          "type": "null"
        },
        {
          "type": "string",
          "format": "email"
        }
      ]
    },
    "customer_name": {
      "anyOf": [
        {
          "type": "null"
        },
        {
          "minLength": 2,
          "type": "string"
        }
      ]
    },
    "customer_phone": {
      "anyOf": [
        {
          "type": "null"
        },
        {
          "type": "string"
        }
      ]
    },
    "intent": {
      "anyOf": [
        {
          "type": "null"
        },
        {
          "type": "string",
          "enum": [
        "book_appointment",
        "reschedule_appointment",
        "cancel_appointment",
        "check_availability",
        null
      ]
        }
      ]
    },
    "missing_fields": {
      "type": "array",
  "items": {
    "type": "string",
    "enum": [
      "service_name",
      "appointment_date",
      "appointment_start_at",
      "customer_name",
      "customer_email",
      "customer_phone"
    ]
  },
  "uniqueItems": true
    },
    "notes": {
      "anyOf": [
        {
          "type": "null"
        },
        {
          "type": "string"
        }
      ]
    },
    "service_name": {
      "anyOf": [
        {
          "type": "null"
        },
        {
          "type": "string"
        }
      ]
    },
    "status": {
      "type": "string",
      "enum": [
        "collecting",
        "confirming",
        "confirmed",
        "cancelled"
      ]
    }
  },
  "required": [
    "appointment_date",
    "appointment_start_at",
    "booking_id",
    "customer_email",
    "customer_name",
    "customer_phone",
    "intent",
    "missing_fields",
    "notes",
    "service_name",
    "status"
  ],
  "additionalProperties": false
}
    $json$);
END $$;