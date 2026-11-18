from glassbox import cli
from glassbox.calibration import CalibrationReport, ExampleResult


def test_reliability_diagram_shows_confidence_and_accuracy_on_the_same_scale():
    report = CalibrationReport(
        results=[
            ExampleResult("benign", "benign", 0.2, True),
            ExampleResult("benign", "suspicious", 0.8, False),
        ]
    )
    diagram = cli._render_reliability_diagram(report)
    assert "0.20-0.30 |....C...............A|" in diagram
    assert "0.80-0.90 |A...............C....|" in diagram
    assert "C = mean confidence" in diagram
    assert "A = accuracy" in diagram
    assert diagram.isascii()


def test_reliability_diagram_marks_overlapping_endpoints():
    report = CalibrationReport(
        results=[
            ExampleResult("benign", "suspicious", 0.0, False),
            ExampleResult("benign", "benign", 1.0, True),
        ]
    )
    diagram = cli._render_reliability_diagram(report)
    assert "0.00-0.10 |*....................|" in diagram
    assert "0.90-1.00 |....................*|" in diagram


def test_calibration_cli_includes_diagram_below_table(capsys):
    assert cli.main(["calibrate", "--demo"]) == 1
    output = capsys.readouterr().out
    assert output.index("reliability diagram") > output.index("| said")
    assert output.index("reliability diagram") < output.index("verdict:")
    assert output.isascii()


def test_calibration_without_predictions_has_no_diagram():
    output = cli._render_calibration(CalibrationReport(results=[], errors=2))
    assert "no scored predictions" in output
    assert "reliability diagram" not in output
