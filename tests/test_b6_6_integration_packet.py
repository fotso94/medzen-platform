import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-002-b6-6-integration-window.md"
READINESS = ROOT / "platform/evidence/B6-6-PACKET-READINESS-2026-001.json"


def test_packet_is_fail_closed_while_release_images_and_adapters_are_missing():
    value = PACKET.read_text()
    assert "DRAFT BLOCKED — NOT APPROVABLE OR EXECUTABLE" in value
    assert "This revision must not be approved or executed" in value
    assert value.count("NOT_AVAILABLE_PACKET_REFUSES") == 4
    assert "No ECR push, IAM change, SSM write, scale-up, ALB or\ndeployment is authorized" in value


def test_packet_names_services_digests_order_isolation_traffic_cost_and_cleanup():
    value = PACKET.read_text()
    for image in (
        "medzen-model-loader", "medzen-asr-runtime", "medzen-nvidia-dra",
        "medzen-rag-index", "medzen-llm-gateway", "medzen-orchestrator",
        "medzen-speech-tts-gateway",
    ):
        assert image in value
    for digest in (
        "cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5",
        "434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087",
        "7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246",
    ):
        assert digest in value
    assert "internal ALB only" in value
    assert "`ClusterIP` only" in value
    assert "97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b" in value
    assert "Maximum wall-clock window: `4 hours`" in value
    assert "Proposed all-in reservation/ceiling: `$10.00`" in value
    assert "CPU minimum `0`, desired `0`, instances `0`, nodes `0`" in value
    assert "GPU minimum `0`, desired `0`, instances `0`, nodes `0`" in value


def test_readiness_record_matches_repository_and_live_empty_repositories():
    value = json.loads(READINESS.read_bytes())
    assert value["status"] == "BLOCKED_LOCAL_RELEASE_ENGINEERING_REQUIRED"
    assert len(value["known_deployable_images"]) == 3
    assert len(value["missing_deployable_images"]) == 4
    assert all(item["ecr_image_count"] == 0 for item in value["missing_deployable_images"].values())
    assert all(item["dockerfile_present"] is False for item in value["missing_deployable_images"].values())
    assert value["unchanged_aws_state"]["cpu_desired"] == 0
    assert value["unchanged_aws_state"]["gpu_desired"] == 0


def test_current_generated_manifests_are_not_misrepresented_as_deployable():
    for path in (ROOT / "platform/k8s/base").glob("*.yaml"):
        if path.name == "asr-runtime.yaml":
            continue
        assert "PLACEHOLDER_TAG" in path.read_text()
