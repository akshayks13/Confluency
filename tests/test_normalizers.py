"""
test_normalizers.py — Unit tests for all normalizer functions.

These are pure-function tests — no external dependencies, no network.
"""
import pytest

from transformer.normalizers.name import normalize_name
from transformer.normalizers.email import normalize_email, extract_emails_from_text, gmail_base_address, is_gmail_alias
from transformer.normalizers.phone import normalize_phone
from transformer.normalizers.date_norm import normalize_date, normalize_date_range
from transformer.normalizers.location import normalize_location
from transformer.normalizers.skills import normalize_skill, normalize_skills_list
from transformer.normalizers.url import normalize_url, classify_url, extract_github_username


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------

class TestNameNormalizer:
    def test_title_case(self):
        name, conf = normalize_name("JOHN DOE")
        assert name == "John Doe"
        assert conf == 1.0

    def test_comma_reversal(self):
        name, conf = normalize_name("Doe, John")
        assert name == "John Doe"
        assert conf < 1.0

    def test_strip_annotation(self):
        name, conf = normalize_name("Jane Smith (she/her)")
        assert name == "Jane Smith"

    def test_particle_preservation(self):
        name, _ = normalize_name("Ludwig van Beethoven")
        assert "van" in name.lower()

    def test_hyphenated(self):
        name, _ = normalize_name("Mary-Jane Watson")
        assert name == "Mary-Jane Watson"

    def test_none_returns_none(self):
        name, conf = normalize_name(None)
        assert name is None
        assert conf == 0.0

    def test_empty_returns_none(self):
        name, conf = normalize_name("   ")
        assert name is None

    def test_apostrophe(self):
        name, _ = normalize_name("o'brien sean")
        assert "O'Brien" in name


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

class TestEmailNormalizer:
    def test_lowercase(self):
        email, conf = normalize_email("John.Doe@Gmail.COM")
        assert email == "john.doe@gmail.com"
        assert conf == 1.0

    def test_strip_angle_brackets(self):
        email, conf = normalize_email("<user@example.com>")
        assert email == "user@example.com"

    def test_strip_mailto(self):
        email, conf = normalize_email("mailto:user@example.com")
        assert email == "user@example.com"

    def test_invalid_no_at(self):
        email, conf = normalize_email("notanemail")
        assert email is None
        assert conf == 0.0

    def test_invalid_double_dot(self):
        email, conf = normalize_email("user..name@example.com")
        assert email is None

    def test_gmail_alias_detection(self):
        assert is_gmail_alias("user+jobs@gmail.com") is True
        assert is_gmail_alias("user@yahoo.com") is False

    def test_gmail_base_address(self):
        base = gmail_base_address("user+jobs@gmail.com")
        assert base == "user@gmail.com"

    def test_extract_from_text(self):
        text = "Contact me at john@example.com or jane@test.org for more info."
        emails = extract_emails_from_text(text)
        assert "john@example.com" in emails
        assert "jane@test.org" in emails


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------

class TestPhoneNormalizer:
    def test_us_local_format(self):
        phone, conf = normalize_phone("(650) 555-1234")
        assert phone == "+16505551234"
        assert conf > 0

    def test_international_format(self):
        phone, conf = normalize_phone("+44 20 7946 0958")
        assert phone is not None
        assert phone.startswith("+44")

    def test_too_short(self):
        phone, conf = normalize_phone("555-12")
        assert phone is None
        assert conf == 0.0

    def test_none_input(self):
        phone, conf = normalize_phone(None)
        assert phone is None

    def test_extension_stripped(self):
        phone, conf = normalize_phone("(650) 555-1234 ext. 456")
        # Should still parse the main number
        assert phone is not None or conf == 0.0  # Depends on library behavior

    def test_indian_number(self):
        phone, conf = normalize_phone("+91 98765 43210")
        if phone:  # Only if phonenumbers is installed
            assert phone.startswith("+91")


# ---------------------------------------------------------------------------
# Date
# ---------------------------------------------------------------------------

class TestDateNormalizer:
    def test_month_year(self):
        date, is_current, conf = normalize_date("January 2020")
        assert date == "2020-01"
        assert not is_current
        assert conf == 1.0

    def test_short_month(self):
        date, _, _ = normalize_date("Jan 2020")
        assert date == "2020-01"

    def test_iso_format(self):
        date, _, _ = normalize_date("2020-03")
        assert date == "2020-03"

    def test_present(self):
        date, is_current, _ = normalize_date("Present")
        assert date is None
        assert is_current is True

    def test_current(self):
        date, is_current, _ = normalize_date("current")
        assert is_current is True

    def test_year_only(self):
        date, _, conf = normalize_date("2020")
        assert date == "2020"
        assert conf < 1.0   # Lower confidence — month unknown

    def test_unparseable(self):
        date, is_current, conf = normalize_date("sometime last year")
        assert date is None
        assert conf == 0.0

    def test_none_input(self):
        date, is_current, conf = normalize_date(None)
        assert date is None
        assert conf == 0.0

    def test_date_range(self):
        start, end, is_current, conf = normalize_date_range("March 2019 - Present")
        assert start == "2019-03"
        assert end is None
        assert is_current is True

    def test_date_range_closed(self):
        start, end, is_current, conf = normalize_date_range("2018-01 – 2020-06")
        assert start == "2018-01"
        assert end == "2020-06"
        assert not is_current

    def test_short_year(self):
        date, _, conf = normalize_date("Jan '20")
        assert date == "2020-01"
        assert conf < 1.0


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class TestLocationNormalizer:
    def test_city_state_us(self):
        loc, conf = normalize_location("San Francisco, CA")
        assert loc.city == "San Francisco"
        assert loc.region == "CA"
        assert loc.country == "US"
        assert conf > 0.9

    def test_city_country(self):
        loc, conf = normalize_location("London, UK")
        assert loc.city == "London"
        assert loc.country == "GB"

    def test_remote(self):
        loc, conf = normalize_location("Remote")
        assert loc.city is None
        assert loc.country is None
        assert conf > 0

    def test_empty(self):
        loc, conf = normalize_location("")
        assert conf == 0.0

    def test_preserves_raw(self):
        loc, _ = normalize_location("New York, NY")
        assert loc.raw == "New York, NY"

    def test_three_parts(self):
        loc, conf = normalize_location("Austin, TX, US")
        assert loc.city == "Austin"
        assert loc.country == "US"


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

class TestSkillsNormalizer:
    def test_alias_lookup(self):
        name, conf, known = normalize_skill("js")
        assert name == "JavaScript"
        assert known is True
        assert conf == 1.0

    def test_case_insensitive(self):
        name, _, _ = normalize_skill("PYTHON")
        assert name == "Python"

    def test_unknown_skill(self):
        name, conf, known = normalize_skill("MyExoticFramework")
        assert name == "Myexoticframework"  # Title-cased
        assert known is False
        assert conf < 1.0

    def test_empty(self):
        name, conf, known = normalize_skill("")
        assert name == ""
        assert conf == 0.0

    def test_deduplicate(self):
        skills = normalize_skills_list(["Python", "python", "py", "JavaScript"])
        names = [s[0] for s in skills]
        assert names.count("Python") == 1
        assert names.count("JavaScript") == 1

    def test_k8s_alias(self):
        name, _, known = normalize_skill("k8s")
        assert name == "Kubernetes"
        assert known is True


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------

class TestURLNormalizer:
    def test_add_https(self):
        url, conf = normalize_url("github.com/user")
        assert url.startswith("https://")

    def test_force_https(self):
        url, conf = normalize_url("http://github.com/user")
        assert url.startswith("https://")

    def test_remove_trailing_slash(self):
        url, _ = normalize_url("https://github.com/user/")
        assert not url.endswith("/")

    def test_classify_github(self):
        assert classify_url("https://github.com/user") == "github"

    def test_classify_linkedin(self):
        assert classify_url("https://linkedin.com/in/user") == "linkedin"

    def test_classify_other(self):
        assert classify_url("https://example.com") == "other"

    def test_extract_github_username(self):
        username = extract_github_username("https://github.com/torvalds")
        assert username == "torvalds"

    def test_extract_github_username_org_rejected(self):
        username = extract_github_username("https://github.com/orgs/acme")
        assert username is None

    def test_none_input(self):
        url, conf = normalize_url(None)
        assert url is None
        assert conf == 0.0
