INSERT INTO tools (id, name, description, input_schema, output_schema)
VALUES (
    gen_random_uuid(),
    'handoff_to_human',
    $desc$Transfer the conversation to a human operator. Use when the user explicitly asks for a person, is frustrated, or the request is outside what you can safely do. Do not use for simple questions you can answer.$desc$,
    $json$
    {
      "type": "object",
      "properties": {
        "reason": {
          "type": "string",
          "enum": [
            "user_requested",
            "complaint_or_escalation",
            "out_of_scope",
            "sensitive_or_legal",
            "payment_or_account_issue",
            "tool_failure",
            "low_confidence"
          ],
          "description": "Primary reason for the handoff"
        },
        "summary": {
          "type": "string",
          "maxLength": 1000,
          "description": "Short summary of the conversation and what the user needs, so the human doesn't have to re-read everything"
        },
        "urgency": {
          "type": "string",
          "enum": ["low", "normal", "high", "critical"]
        },
        "user_sentiment": {
          "type": ["string", "null"],
          "description": "Current sentiment of the user, or null if unknown",
          "enum": ["positive", "neutral", "frustrated", "angry", null]
        }
      },
      "required": ["reason", "summary", "urgency", "user_sentiment"],
      "additionalProperties": false
    }
    $json$::jsonb,
    $json$
    {
      "type": "object",
      "properties": {
        "status": {
          "type": "string",
          "enum": [
            "connected",
            "queued",
            "scheduled_callback",
            "unavailable",
            "rejected",
            "error"
          ],
          "description": "Outcome of the handoff request"
        },
        "channel": {
          "type": ["string", "null"],
          "description": "How the human will contact the user",
          "enum": ["live_chat", "email", "phone_callback", "ticket", null]
        },
        "message_for_user": {
          "type": "string",
          "description": "Suggested wording to relay to the user"
        },
        "error": {
          "type": ["object", "null"],
          "properties": {
            "code": { "type": "string" },
            "message": { "type": "string" },
            "retryable": { "type": "boolean" }
          },
          "required": ["code", "message", "retryable"],
          "additionalProperties": false
        }
      },
      "required": ["status", "message_for_user", "channel"],
      "additionalProperties": false
    }
    $json$::jsonb
);