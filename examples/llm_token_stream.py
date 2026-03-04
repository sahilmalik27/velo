"""
LLM token stream example - stateful post-processing.

Demonstrates: accumulating tokens, sentence detection, word counting
"""

import asyncio
from streamfn import stream_fn


@stream_fn
async def accumulate_sentences(events):
    """Accumulate tokens into complete sentences."""
    buffer = ""
    sentence_terminators = {".", "!", "?"}

    async for token in events:
        buffer += token

        # Check if we have a complete sentence
        if any(buffer.endswith(term) for term in sentence_terminators):
            sentence = buffer.strip()
            buffer = ""
            yield {"sentence": sentence, "length": len(sentence.split())}


@stream_fn
async def count_words(events):
    """Running word count across sentences."""
    total_words = 0

    async for event in events:
        total_words += event["length"]
        yield {
            "sentence": event["sentence"],
            "sentence_words": event["length"],
            "total_words": total_words,
        }


@stream_fn
async def filter_long_sentences(events):
    """Filter sentences with more than N words."""
    min_words = 5

    async for event in events:
        if event["sentence_words"] >= min_words:
            yield event


# Compose pipeline
llm_pipeline = accumulate_sentences | count_words | filter_long_sentences


async def main():
    """Run the LLM token stream example."""

    # Simulate LLM token stream (OpenAI-style)
    async def llm_tokens():
        """Simulate streaming tokens from an LLM."""
        text = (
            "Hello there. This is a test. "
            "Stream functions are stateful generators. "
            "They remember state between events. "
            "Fast! "
            "This makes them perfect for processing LLM outputs."
        )

        for token in text.split():
            yield token + " "
            await asyncio.sleep(0.01)  # Simulate streaming delay

    # Batch mode
    print("Processing LLM output (batch)...")
    results = await llm_pipeline.run([
        "Hello", " world", ".", " This", " is", " a", " test", ".",
        " Stream", " functions", " are", " awesome", "!"
    ])

    for r in results:
        print(f"  {r['sentence']} ({r['sentence_words']} words, total: {r['total_words']})")

    # Live mode
    print("\nProcessing LLM output (live stream)...")
    async with llm_pipeline.open() as stream:
        async for result in stream.feed(llm_tokens()):
            print(f"  {result['sentence']}")
            print(f"    Words: {result['sentence_words']} | Total: {result['total_words']}")

        print(f"\nStream metrics:")
        print(f"  Events in: {stream.metrics.events_in}")
        print(f"  Events out: {stream.metrics.events_out}")
        print(f"  Duration: {stream.metrics.duration_ms:.1f}ms")


if __name__ == "__main__":
    asyncio.run(main())
