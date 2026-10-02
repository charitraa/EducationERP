from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ApiKeyScheme(OpenApiAuthenticationExtension):
    target_class = "core.api_keys.authentication.ApiKeyAuthentication"
    name = "ApiKey"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "Authorization",
                "description": "`Api-Key erp_<prefix>_<secret>` (or the X-API-Key header)."}
