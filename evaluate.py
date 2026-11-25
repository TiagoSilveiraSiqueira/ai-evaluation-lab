#!/usr/bin/env python3
"""Checks estruturados e determinísticos. Não usa rede nem avalia texto livre."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


RESPONSE_FIELDS = {
    "case_id", "category", "evidence_ids", "numeric_facts",
    "missing_info", "cause_id", "priority",
}
CHECKS = (
    "schema", "category", "evidence_ids", "numeric_facts",
    "missing_info", "cause", "priority", "abstention",
)
SOURCE_TYPES = {"illustrative_fixture", "local_output"}
PRIORITY_LEVELS = {"P1", "P2", "P3"}


class EvaluationInputError(ValueError):
    """Erro de entrada cuja mensagem não contém valores da resposta recebida."""


def is_number(value: Any) -> bool:
    """bool não é aceito como número, embora seja subclasse de int em Python."""
    return type(value) is int or (type(value) is float and math.isfinite(value))


def string_list(value: Any, *, allow_empty: bool = True) -> bool:
    if type(value) is not list:
        return False
    if not allow_empty and not value:
        return False
    if not all(type(item) is str and bool(item.strip()) for item in value):
        return False
    return len(value) == len(set(value))


def numeric_object(value: Any) -> bool:
    return (
        type(value) is dict
        and all(type(key) is str and bool(key.strip()) for key in value)
        and all(is_number(item) for item in value.values())
    )


def priority_object(value: Any) -> bool:
    if value is None:
        return True
    return (
        type(value) is dict
        and set(value) == {"rule_id", "level"}
        and type(value["rule_id"]) is str
        and bool(value["rule_id"].strip())
        and type(value["level"]) is str
        and value["level"] in PRIORITY_LEVELS
    )


def _reject_constant(_value: str) -> None:
    raise EvaluationInputError("JSON inválido: números não finitos não são permitidos.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationInputError("JSON inválido: chave duplicada em um objeto.")
        result[key] = value
    return result


def load_json(path: Path, label: str) -> Any:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise EvaluationInputError(f"Não foi possível ler o arquivo de {label} em UTF-8.") from None
    try:
        return json.loads(
            content, parse_constant=_reject_constant, object_pairs_hook=_unique_object
        )
    except json.JSONDecodeError as error:
        raise EvaluationInputError(
            f"JSON inválido no arquivo de {label}: linha {error.lineno}, coluna {error.colno}."
        ) from None


def validate_dataset(dataset: Any) -> None:
    if type(dataset) is not dict:
        raise EvaluationInputError("Dataset deve ser um objeto JSON.")
    if type(dataset.get("format_version")) is not int or dataset["format_version"] != 1:
        raise EvaluationInputError("Dataset exige format_version inteiro igual a 1.")
    for field in ("categories", "missing_info_codes", "confirmed_cause_ids"):
        if not string_list(dataset.get(field), allow_empty=field != "categories"):
            raise EvaluationInputError(f"Dataset exige lista válida e sem duplicatas em {field}.")
    cases = dataset.get("cases")
    if type(cases) is not list or not cases:
        raise EvaluationInputError("Dataset exige uma lista não vazia de casos.")
    seen_cases: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"Dataset, caso na posição {index}"
        if type(case) is not dict or type(case.get("case_id")) is not str or not case["case_id"].strip():
            raise EvaluationInputError(f"{prefix}: case_id inválido.")
        if case["case_id"] in seen_cases:
            raise EvaluationInputError(f"{prefix}: case_id duplicado.")
        seen_cases.add(case["case_id"])
        evidence = case.get("evidence")
        if type(evidence) is not list or not evidence:
            raise EvaluationInputError(f"{prefix}: evidências ausentes.")
        evidence_ids: set[str] = set()
        policy_ids: set[str] = set()
        confirmed_ids: set[str] = set()
        source_facts: dict[str, Any] = {}
        for item in evidence:
            if type(item) is not dict or type(item.get("id")) is not str or not item["id"].strip():
                raise EvaluationInputError(f"{prefix}: ID de evidência inválido.")
            if item["id"] in evidence_ids:
                raise EvaluationInputError(f"{prefix}: ID de evidência duplicado.")
            evidence_ids.add(item["id"])
            if item.get("type") == "policy":
                policy_ids.add(item["id"])
            cause = item.get("confirmed_cause_id")
            if cause is not None:
                if type(cause) is not str or cause not in dataset["confirmed_cause_ids"]:
                    raise EvaluationInputError(f"{prefix}: confirmação de causa inválida.")
                confirmed_ids.add(cause)
            facts = item.get("numeric_facts", {})
            if not numeric_object(facts):
                raise EvaluationInputError(f"{prefix}: fatos numéricos inválidos nas evidências.")
            if set(facts) & set(source_facts):
                raise EvaluationInputError(f"{prefix}: chave numérica repetida nas evidências.")
            source_facts.update(facts)
        expected = case.get("expected")
        expected_fields = {
            "admitted_categories", "evidence_ids", "numeric_facts",
            "missing_info", "cause_id", "priority",
        }
        if type(expected) is not dict or set(expected) != expected_fields:
            raise EvaluationInputError(f"{prefix}: contrato esperado incompleto ou com campos extras.")
        admitted = expected["admitted_categories"]
        if not string_list(admitted, allow_empty=False) or not set(admitted) <= set(dataset["categories"]):
            raise EvaluationInputError(f"{prefix}: categorias esperadas inválidas.")
        refs = expected["evidence_ids"]
        if not string_list(refs, allow_empty=False) or not set(refs) <= evidence_ids:
            raise EvaluationInputError(f"{prefix}: referências esperadas inválidas.")
        if not numeric_object(expected["numeric_facts"]) or expected["numeric_facts"] != source_facts:
            raise EvaluationInputError(f"{prefix}: fatos esperados devem corresponder às evidências.")
        missing = expected["missing_info"]
        if not string_list(missing) or not set(missing) <= set(dataset["missing_info_codes"]):
            raise EvaluationInputError(f"{prefix}: códigos de lacunas esperados inválidos.")
        expected_cause = expected["cause_id"]
        if expected_cause is not None and (type(expected_cause) is not str or expected_cause not in confirmed_ids):
            raise EvaluationInputError(f"{prefix}: causa esperada exige confirmação explícita.")
        if not priority_object(expected["priority"]):
            raise EvaluationInputError(f"{prefix}: prioridade esperada inválida.")
        if expected["priority"] is not None and expected["priority"]["rule_id"] not in policy_ids:
            raise EvaluationInputError(f"{prefix}: prioridade esperada exige regra nas evidências.")


def validate_envelope(document: Any) -> None:
    if type(document) is not dict or set(document) != {"format_version", "source_type", "responses"}:
        raise EvaluationInputError("Respostas exigem objeto com format_version, source_type e responses, sem campos extras.")
    if type(document["format_version"]) is not int or document["format_version"] != 1:
        raise EvaluationInputError("Respostas exigem format_version inteiro igual a 1.")
    if type(document["source_type"]) is not str or document["source_type"] not in SOURCE_TYPES:
        raise EvaluationInputError("source_type deve ser illustrative_fixture ou local_output.")
    if type(document["responses"]) is not list:
        raise EvaluationInputError("responses deve ser uma lista, que pode estar vazia.")


def schema_errors(response: dict[str, Any], dataset: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing_fields = RESPONSE_FIELDS - set(response)
    if missing_fields:
        errors.append("Campos obrigatórios ausentes: " + ", ".join(sorted(missing_fields)) + ".")
    if set(response) - RESPONSE_FIELDS:
        errors.append("Campos extras não são permitidos; nomes e valores recebidos foram omitidos.")
    if type(response.get("case_id")) is not str or not response["case_id"].strip():
        errors.append("case_id deve ser uma string não vazia.")
    if type(response.get("category")) is not str or response["category"] not in dataset["categories"]:
        errors.append("category deve ser uma das categorias declaradas no dataset.")
    if not string_list(response.get("evidence_ids"), allow_empty=False):
        errors.append("evidence_ids exige strings não vazias e sem duplicatas.")
    if not numeric_object(response.get("numeric_facts")):
        errors.append("numeric_facts exige objeto de números finitos; booleanos e strings não são números.")
    missing_info = response.get("missing_info")
    if not string_list(missing_info) or not set(missing_info) <= set(dataset["missing_info_codes"]):
        errors.append("missing_info exige códigos declarados no dataset, sem duplicatas.")
    if "cause_id" in response and response["cause_id"] is not None:
        if type(response["cause_id"]) is not str or not response["cause_id"].strip():
            errors.append("cause_id deve ser null ou uma string não vazia.")
    if "priority" in response and not priority_object(response["priority"]):
        errors.append("priority deve ser null ou objeto com rule_id e level válidos, sem campos extras.")
    return errors


def same_string_ids(actual: Any, expected: list[str], *, allow_empty: bool = True) -> bool:
    return string_list(actual, allow_empty=allow_empty) and set(actual) == set(expected)


def numeric_facts_match(actual: Any, expected: dict[str, Any]) -> bool:
    return numeric_object(actual) and set(actual) == set(expected) and all(
        actual[key] == value for key, value in expected.items()
    )


def numeric_fact_errors(actual: Any, expected: dict[str, Any]) -> list[str]:
    if type(actual) is not dict:
        return ["numeric_facts está ausente ou não é um objeto."]
    errors: list[str] = []
    for key in expected:
        if key not in actual:
            errors.append(f"Campo numérico esperado {key} está ausente.")
        elif not is_number(actual[key]):
            errors.append(f"Campo {key} exige número finito; valor recebido omitido.")
        elif actual[key] != expected[key]:
            errors.append(f"Campo {key} diverge do valor esperado no dataset; valor recebido omitido.")
    if set(actual) - set(expected):
        errors.append("Há campos numéricos extras; nomes e valores recebidos foram omitidos.")
    return errors


def requires_abstention(expected: dict[str, Any]) -> bool:
    return expected["cause_id"] is None or expected["priority"] is None


def abstention_matches(response: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(
        field in response and response[field] is None
        for field in ("cause_id", "priority") if expected[field] is None
    )


def _metric(values: list[bool]) -> dict[str, Any]:
    passed = sum(values)
    total = len(values)
    return {"passed": passed, "total": total, "rate": round(passed / total, 6) if total else None}


def evaluate_responses(dataset: Any, document: Any) -> dict[str, Any]:
    validate_dataset(dataset)
    validate_envelope(document)
    known_ids = {case["case_id"] for case in dataset["cases"]}
    indexed: dict[str, list[dict[str, Any]]] = {}
    global_errors: list[str] = []
    ignored_entries = 0
    for index, response in enumerate(document["responses"]):
        if type(response) is not dict or type(response.get("case_id")) is not str:
            global_errors.append(f"responses[{index}]: objeto ou case_id inválido; conteúdo recebido omitido.")
            ignored_entries += 1
            continue
        case_id = response["case_id"]
        if case_id not in known_ids:
            global_errors.append(f"responses[{index}]: case_id não existe no dataset; valor recebido omitido.")
            ignored_entries += 1
            continue
        indexed.setdefault(case_id, []).append(response)
    results: list[dict[str, Any]] = []
    messages = {
        "category": "Categoria não corresponde às categorias admissíveis deste caso.",
        "evidence_ids": "Referências devem conter exatamente os IDs esperados, sem invenções ou duplicatas.",
        "numeric_facts": "Fatos numéricos devem conter exatamente as chaves e os valores esperados, sem booleanos.",
        "missing_info": "Lacunas devem corresponder exatamente aos códigos esperados, sem omissões ou acréscimos.",
        "cause": "cause_id diverge da confirmação explícita esperada ou do null exigido.",
        "priority": "priority diverge da regra explícita esperada ou do null exigido.",
        "abstention": "Campo sem evidência suficiente deve estar presente e conter null; ausência não é abstenção.",
    }
    for case in dataset["cases"]:
        expected = case["expected"]
        applicable = requires_abstention(expected)
        entries = indexed.get(case["case_id"], [])
        if len(entries) != 1:
            checks: dict[str, bool | None] = {name: False for name in CHECKS}
            checks["abstention"] = False if applicable else None
            message = "Resposta ausente para este caso." if not entries else "Mais de uma resposta para este caso; nenhuma foi selecionada."
            errors = [{"check": "response", "message": message}]
        else:
            response = entries[0]
            structural_errors = schema_errors(response, dataset)
            checks = {
                "schema": not structural_errors,
                "category": type(response.get("category")) is str and response["category"] in expected["admitted_categories"],
                "evidence_ids": same_string_ids(response.get("evidence_ids"), expected["evidence_ids"], allow_empty=False),
                "numeric_facts": numeric_facts_match(response.get("numeric_facts"), expected["numeric_facts"]),
                "missing_info": same_string_ids(response.get("missing_info"), expected["missing_info"]),
                "cause": "cause_id" in response and response["cause_id"] == expected["cause_id"],
                "priority": "priority" in response and priority_object(response["priority"]) and response["priority"] == expected["priority"],
                "abstention": abstention_matches(response, expected) if applicable else None,
            }
            errors = [{"check": "schema", "message": message} for message in structural_errors]
            errors.extend({"check": name, "message": messages[name]} for name in CHECKS if name not in {"schema", "numeric_facts"} and checks[name] is False)
            if checks["numeric_facts"] is False:
                errors.extend({"check": "numeric_facts", "message": message} for message in numeric_fact_errors(response.get("numeric_facts"), expected["numeric_facts"]))
        results.append({
            "case_id": case["case_id"],
            "checks": checks,
            "all_checks_passed": all(value for value in checks.values() if value is not None),
            "errors": errors,
        })
    metrics = {
        name: _metric([result["checks"][name] for result in results if result["checks"][name] is not None])
        for name in CHECKS
    }
    metrics["all_checks"] = _metric([result["all_checks_passed"] for result in results])
    notice = (
        "Respostas ilustrativas escritas à mão. Scores medem estas fixtures, não execuções nem ganhos de LLM."
        if document["source_type"] == "illustrative_fixture"
        else "Checks do contrato e do gabarito estruturado. Não avaliam verdade de texto livre nem eficácia geral de LLM."
    )
    return {
        "report_version": 1,
        "dataset_sha256": hashlib.sha256(json.dumps(dataset, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest(),
        "source_type": document["source_type"],
        "notice": notice,
        "dataset_cases": len(dataset["cases"]),
        "received_responses": len(document["responses"]),
        "ignored_response_entries": ignored_entries,
        "global_errors": global_errors,
        "metrics": metrics,
        "cases": results,
        "passed": not global_errors and metrics["all_checks"]["passed"] == len(results),
    }


def compare_reports(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    if base["dataset_sha256"] != other["dataset_sha256"]:
        raise EvaluationInputError("Comparação exige o mesmo dataset e gabarito.")
    deltas = {}
    for name in base["metrics"]:
        first = base["metrics"][name]
        second = other["metrics"][name]
        if first["total"] != second["total"]:
            raise EvaluationInputError("Comparação exige os mesmos denominadores e dataset.")
        delta = None if first["rate"] is None else round(
            (second["passed"] - first["passed"]) / first["total"] * 100, 6
        )
        deltas[name] = {
            "passed_difference": second["passed"] - first["passed"],
            "denominator": first["total"],
            "percentage_point_difference": delta,
        }
    return {
        "comparison_version": 1,
        "notice": "Comparação reproduzível de checks estruturados no mesmo dataset; não demonstra ganhos gerais de LLM.",
        "base_report": base,
        "comparison_report": other,
        "deltas": deltas,
        "passed": base["passed"] and other["passed"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Avalia respostas JSON sem rede, chaves ou judge LLM.")
    parser.add_argument("--responses", type=Path, required=True, help="JSON do contrato de respostas.")
    parser.add_argument("--dataset", type=Path, required=True, help="Dataset JSON com gabarito estruturado.")
    parser.add_argument("--compare", type=Path, help="Segundo JSON de respostas para o mesmo dataset.")
    parser.add_argument("--output", type=Path, help="Salvar relatório em JSON; prefira local/ (ignorado pelo Git).")
    parser.add_argument("--require-all", action="store_true", help="Retorna código 1 se qualquer documento avaliado não passar em todos os checks.")
    args = parser.parse_args(argv)
    try:
        dataset = load_json(args.dataset, "dataset")
        document = load_json(args.responses, "respostas")
        result = evaluate_responses(dataset, document)
        if args.compare:
            other = evaluate_responses(dataset, load_json(args.compare, "comparação"))
            result = compare_reports(result, other)
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + "\n", encoding="utf-8")
            except OSError:
                raise EvaluationInputError("Não foi possível salvar o relatório solicitado.") from None
        else:
            print(rendered)
        return 1 if args.require_all and not result["passed"] else 0
    except EvaluationInputError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
