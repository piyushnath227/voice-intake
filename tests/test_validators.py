import pytest
from datetime import date, timedelta
from app import validators as V

def test_names_normalised_and_checked():
    assert V.FIELD_RULES["first_name"]("jane") == "Jane"
    assert V.FIELD_RULES["last_name"]("o'brien") == "O'Brien"
    assert V.FIELD_RULES["last_name"]("Smith-Jones") == "Smith-Jones"
    with pytest.raises(ValueError): V.FIELD_RULES["first_name"]("J4ne")
    with pytest.raises(ValueError): V.FIELD_RULES["first_name"]("a" * 51)

def test_dob_rules():
    assert V.dob("04/12/1990") == date(1990, 4, 12)
    assert V.dob("1990-04-12") == date(1990, 4, 12)
    with pytest.raises(ValueError, match="future"): V.dob((date.today() + timedelta(days=1)).strftime("%m/%d/%Y"))
    with pytest.raises(ValueError, match="real calendar"): V.dob("02/30/1990")
    with pytest.raises(ValueError): V.dob("13/01/1990")

def test_phone_rules():
    assert V.phone("+1 (212) 555-0142") == "2125550142"
    with pytest.raises(ValueError, match="3 digits"): V.phone("555")
    with pytest.raises(ValueError): V.phone("1235550142")   # area code starts with 1

def test_state_zip_email_sex():
    assert V.state("california") == "CA" and V.state("tx") == "TX"
    with pytest.raises(ValueError): V.state("Narnia")
    assert V.zip_code("12345") == "12345" and V.zip_code("123456789") == "12345-6789"
    with pytest.raises(ValueError): V.zip_code("1234")
    assert V.email("Jane@Example.com") == "jane@example.com" and V.email("") is None
    with pytest.raises(ValueError): V.email("jane@")
    assert V.sex("prefer not to say") == "Decline to Answer"
    with pytest.raises(ValueError): V.sex("banana")

def test_sanitisation_strips_markup():
    assert V.FIELD_RULES["address_line_1"]("<b>5 Elm</b>   St") == "b5 Elm/b St"
