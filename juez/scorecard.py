import argparse
import json
import sys

from juez.judge import run_judge


DEFAULT_INPUT = "What is 2 + 2?"
DEFAULT_OUTPUT = "The answer is 4."


def _bar(score: float, width: int = 20) -> str:
    score = max(0.0, min(10.0, score))
    filled = int(round((score / 10.0) * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _grade(score: float) -> str:
    if score >= 9.0:
        return "A"
    if score >= 8.0:
        return "B"
    if score >= 7.0:
        return "C"
    if score >= 6.0:
        return "D"
    return "F"


def _to_float(value, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid score for {name}: {value!r}")


def _run_judge_or_exit(input_text: str, output_text: str) -> dict:
    try:
        return run_judge(input_text=input_text, output_text=output_text)
    except Exception as exc:
        print("ERROR: Failed to run judge.", file=sys.stderr)
        print(f"Details: {exc}", file=sys.stderr)
        print("Hint: ensure OPENAI_API_KEY is set in your environment.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the LLM judge and print a readable scorecard."
    )
    parser.add_argument(
        "--input",
        dest="input_text",
        default=DEFAULT_INPUT,
        help="Original prompt/input text",
    )
    parser.add_argument(
        "--output",
        dest="output_text",
        default=DEFAULT_OUTPUT,
        help="Model output text",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON output only",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=7.0,
        help="Pass threshold for average score",
    )

    args = parser.parse_args()

    result = _run_judge_or_exit(args.input_text, args.output_text)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    relevance = _to_float(result.get("relevance"), "relevance")
    clarity = _to_float(result.get("clarity"), "clarity")
    correctness = _to_float(result.get("correctness"), "correctness")
    overall = _to_float(result.get("overall"), "overall")
    average = (relevance + clarity + correctness + overall) / 4.0

    print("AI Evaluation Scorecard")
    print("-----------------------")
    print(f"relevance   {_bar(relevance)} {relevance:.1f}/10")
    print(f"clarity     {_bar(clarity)} {clarity:.1f}/10")
    print(f"correctness {_bar(correctness)} {correctness:.1f}/10")
    print(f"overall     {_bar(overall)} {overall:.1f}/10")
    print()
    print(
        f"average     {average:.2f}/10  "
        f"grade { _grade(average) }  "
        f"pass {average >= args.threshold}"
    )
    print()
    print("recommendation:")
    print(result.get("recommendation", ""))


if __name__ == "__main__":
    main()
