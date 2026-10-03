from app.evals.checks import valid_output

def test_valid_answer():
    assert valid_output({}, {})["score"] == 1

def test_valid_answer2():
    assert valid_output({}, {})["score"] == '0'