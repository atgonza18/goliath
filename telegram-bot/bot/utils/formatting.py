def chunk_message(text: str, max_len: int = 4000) -> list[str]:
    """
    Split a long message into Telegram-safe chunks.

    Tries to split on newlines to avoid breaking mid-sentence.
    Telegram's limit is 4096 characters; we use 4000 for safety margin.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Find a good split point (newline near the limit)
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1 or split_at < max_len // 2:
            split_at = max_len

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks
