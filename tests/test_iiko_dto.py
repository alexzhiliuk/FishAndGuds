from app.integrations.iiko.dto import IikoOrganization


def test_organization_website_is_read_from_supported_iiko_shapes():
    direct = IikoOrganization.model_validate({"id": "1", "name": "One", "websiteUrl": "https://one.example.com"})
    nested = IikoOrganization.model_validate({"id": "2", "name": "Two", "additionalInfo": {"website": "https://two.example.com"}})

    assert direct.website_url == "https://one.example.com"
    assert nested.website_url == "https://two.example.com"
