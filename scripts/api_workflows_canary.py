"""Run the three bounded F3 canaries against official DeepSeek Flash."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

from lean_exposition.runtime import ApiConfig, ApiToolExecutor, FunctionTool, StructuredExecutor
from lean_exposition.workflows import EetDraftRequest, EetWorkflow, NamingWorkflow, ReaderTaskWorkflow


def load_env(path: Path) -> None:
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ[key.strip()] = value.strip().strip("\"'")


def call_record(call):
    return {
        "stage": call.stage,
        "status": call.execution.status,
        "data": call.execution.data,
        "error": asdict(call.execution.error) if call.execution.error else None,
        "usage": asdict(call.execution.usage),
        "requested_model": call.execution.requested_model,
        "response_model": call.execution.response_model,
        "protocol": call.execution.protocol,
        "provider_status": call.execution.provider_status,
        "finish_reason": call.execution.finish_reason,
        "prefix_digest": call.prefix_digest,
        "prompt_digest": call.prompt_digest,
    }


SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "lead_in": {"type": "string"},
        "synopsis": {"type": "string"},
        "lead_out": {"type": "string"},
        "anchors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "part": {"type": "string", "enum": ["lead_in", "synopsis", "lead_out"]},
                    "targets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["part", "targets"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["lead_in", "synopsis", "lead_out", "anchors"],
    "additionalProperties": False,
}


READER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "used_reference": {"type": "string"},
    },
    "required": ["answer", "used_reference"],
    "additionalProperties": False,
}


def run(output: Path, credential_file: Path) -> dict:
    load_env(credential_file)
    config = ApiConfig(
        model="deepseek-flash",
        credential_env="DEEPSEEK_API_KEY",
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        protocol="responses",
        timeout=180,
        max_output_tokens=8192,
        prompt_cache_key="lean-exposition-f3-canary",
    )
    structured = StructuredExecutor(config)
    validation_config = ApiConfig(
        model=config.model,
        credential_env=config.credential_env,
        base_url=config.base_url,
        protocol=config.protocol,
        timeout=config.timeout,
        max_output_tokens=16384,
        prompt_cache_key=config.prompt_cache_key,
    )
    started = time.monotonic()

    naming = NamingWorkflow(structured, locale="en").name(
        kind="region",
        material={
            "public_results": ["Every finite set has a well-defined cardinality."],
            "internal_role": "introduces finite cardinality and the disjoint-union rule",
        },
    )

    tool = FunctionTool(
        "lookup_fact",
        "Look up one fact from the fixed canary reference.",
        {
            "type": "object",
            "properties": {"reference": {"enum": ["finite_union"]}},
            "required": ["reference"],
            "additionalProperties": False,
        },
    )
    reader = ReaderTaskWorkflow(
        ApiToolExecutor(config),
        tools=[tool],
        handlers={"lookup_fact": lambda reference: {"reference": reference, "text": globals_reference[reference]}},
        max_steps=3,
    ).run_reader_task(
        task={
            "question": "What correction term appears when adding the sizes of two finite sets?",
            "required_reference": "finite_union",
        },
        output_schema=READER_SCHEMA,
    )

    requests = [
        EetDraftRequest(
            node_id="disjoint_union",
            locale="en",
            material={
                "node": {"title": "Disjoint union"},
                "cards": [
                    {
                        "statement": "If A and B are finite and disjoint, then |A union B|=|A|+|B|.",
                        "proof": "Split the union into its two disjoint parts.",
                    }
                ],
                "incoming": [],
                "outgoing": ["inclusion_exclusion"],
                "internal": [],
            },
            context={
                "available_facts": [],
                "child_index": 0,
                "ordered_children": ["disjoint_union", "inclusion_exclusion"],
                "allowed_node_anchors": ["inclusion_exclusion"],
                "future_goal": "Account for overlap between two finite sets.",
            },
            schema=SECTION_SCHEMA,
        ),
        EetDraftRequest(
            node_id="inclusion_exclusion",
            locale="en",
            material={
                "node": {"title": "Two-set inclusion-exclusion"},
                "cards": [
                    {
                        "statement": "For finite A and B, |A union B|=|A|+|B|-|A intersection B|.",
                        "proof": "Remove the double-counted intersection from the sum.",
                    }
                ],
                "incoming": ["disjoint_union"],
                "outgoing": [],
                "internal": [],
            },
            context={
                "available_facts": [
                    {"node_id": "disjoint_union", "role": "preceding_outcome", "text": "Cardinality is additive on disjoint unions."}
                ],
                "child_index": 1,
                "ordered_children": ["disjoint_union", "inclusion_exclusion"],
                "allowed_node_anchors": ["disjoint_union"],
                "future_goal": "Account for overlap between two finite sets.",
            },
            schema=SECTION_SCHEMA,
        ),
    ]
    eet = EetWorkflow(
        structured,
        validator_executor=StructuredExecutor(validation_config),
        max_workers=2,
    ).generate_group(
        requests,
        parent_lead_in="Let A and B be finite subsets of a fixed set.",
        parent_lead_out="The overlap term is the precise correction to additive counting.",
    )

    report = {
        "model": config.model,
        "base_url": config.base_url,
        "manual_prose_edits": False,
        "reasoning_rewrite": False,
        "seconds": time.monotonic() - started,
        "naming": call_record(naming),
        "reader": call_record(reader),
        "eet": {
            "drafts": [call_record(call) for call in eet.drafts],
            "stitching": call_record(eet.stitching) if eet.stitching else None,
            "validation": call_record(eet.validation) if eet.validation else None,
            "succeeded": eet.succeeded,
            "cache": eet.evidence,
        },
    }
    report["passed"] = (
        naming.execution.status == "succeeded"
        and reader.execution.status == "succeeded"
        and eet.succeeded
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


globals_reference = {
    "finite_union": "For finite sets A and B, |A union B| = |A| + |B| - |A intersection B|."
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--credential-file",
        type=Path,
        default=Path("/root/.config/lean-exposition/model-providers.env"),
    )
    args = parser.parse_args()
    result = run(args.output, args.credential_file)
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "model": result["model"],
                "seconds": result["seconds"],
                "statuses": {
                    "naming": result["naming"]["status"],
                    "reader": result["reader"]["status"],
                    "eet": result["eet"]["succeeded"],
                },
                "eet_cache": result["eet"]["cache"],
            },
            ensure_ascii=False,
        )
    )
