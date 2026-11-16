// Apply only to service status and error messages, never to a user's story text.
export function systemMessage(value:unknown,replacement='所选模型'):string {
  return String(value ?? '').replace(/\b(?:qwen(?:[\s._-]+(?:flash|max|plus|turbo)|[0-9][\w.-]*)|deepseek(?:[\s._-]+[\w.-]+)|wan[0-9][\w.-]*|veo[\s._-]*[0-9][\w.-]*|seedance[\s._-]*[0-9][\w.-]*|gpt[\s._-]*[0-9][\w.-]*|claude[\s._-]*[0-9][\w.-]*)\b/gi,replacement);
}
