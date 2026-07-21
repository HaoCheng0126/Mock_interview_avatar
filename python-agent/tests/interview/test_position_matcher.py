"""Tests for interview.position_matcher — session-start role/JD matching."""

from interview.models import PositionConfig
from interview.position_matcher import match_position


def _pos(name, keywords):
    return PositionConfig(name=name, match_keywords=keywords)


def test_no_positions_returns_none():
    assert match_position([], jd_text="python 后端") is None


def test_single_position_requires_a_real_match():
    # A lone bank position is NOT force-applied: an unrelated JD (e.g. a 产品经理)
    # must not be evaluated against a 后端 position — that was the residual-leak bug.
    only = _pos("后端工程师", ["python", "后端"])
    assert match_position([only], jd_text="负责产品需求分析与用户增长") is None


def test_single_position_matches_when_jd_is_relevant():
    only = _pos("后端工程师", ["python", "后端"])
    assert match_position([only], jd_text="需要 Python 后端开发经验") is only


def test_matches_by_keyword_in_jd():
    backend = _pos("后端工程师", ["python", "高并发"])
    frontend = _pos("前端工程师", ["react", "css"])
    assert match_position([frontend, backend], jd_text="需要 Python 高并发经验") is backend


def test_matches_by_position_name_in_jd():
    backend = _pos("后端工程师", ["python"])
    data = _pos("数据工程师", ["spark"])
    assert match_position([backend, data], jd_text="招聘数据工程师，负责数仓建设") is data


def test_returns_none_when_nothing_scores():
    # No genuine match anywhere in the bank → None (interview runs off the JD),
    # rather than forcing an arbitrary position onto the candidate.
    a = _pos("岗位A", ["xyz"])
    b = _pos("岗位B", ["qrs"])
    assert match_position([a, b], jd_text="毫不相干的描述") is None


def test_scoring_is_case_insensitive():
    backend = _pos("Backend", ["Python"])
    other = _pos("Other", ["java"])
    assert match_position([other, backend], jd_text="我用 PYTHON 写服务") is backend


def test_empty_jd_can_match_from_target_role():
    backend = _pos("Python 后端工程师", ["后端", "python"])
    product = _pos("产品经理", ["产品"])
    assert (
        match_position(
            [backend, product],
            target_role="Python 后端工程师",
            jd_text="",
        )
        is backend
    )
