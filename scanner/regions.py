"""Country code (ISO 3166-1 alpha-2) -> region key used in config.json.

Countries not listed here end up in the "other" region.
Non-EU European countries (UK, Norway, Switzerland, Iceland, the Balkans ...)
are placed in "europe" on purpose, so cheap city breaks don't flood the
main world list. Move a country to another region by editing this file.
"""

_REGIONS = {
    "europe": """
        AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE
        GB NO CH IS LI MC SM VA AD GI FO AX IM JE GG
        AL BA ME MK RS XK MD UA BY RU
    """,
    "caucasus_central_asia": "GE AM AZ KZ UZ KG TJ TM",
    "middle_east": "AE QA OM BH KW SA JO IL PS LB IQ IR YE SY",
    "north_africa": "MA EG TN DZ LY",
    "africa": """
        CV SN GM GW GN SL LR CI GH TG BJ NG NE ML BF MR CM TD CF GQ GA CG CD AO
        ST SD SS ET ER DJ SO KE UG RW BI TZ ZM MW MZ ZW BW NA ZA LS SZ EH SH
    """,
    "indian_ocean": "MV MU SC RE MG KM YT",
    "south_asia": "IN LK NP BD PK BT AF",
    "southeast_asia": "TH VN ID MY SG PH KH LA MM BN TL",
    "east_asia": "JP KR KP CN HK TW MO MN",
    "north_america": "US CA GL PM BM",
    "caribbean_central": """
        MX GT BZ HN SV NI CR PA CU JM HT DO PR BS BB TT AW CW BQ SX MF BL GP MQ
        LC GD VC AG KN DM KY TC VG VI AI MS
    """,
    "south_america": "BR AR CL PE CO EC BO PY UY VE GY SR GF FK",
    "oceania": """
        AU NZ FJ PF NC PG VU WS TO CK GU MP PW FM MH KI SB NR TV NU WF AS
    """,
}

COUNTRY_TO_REGION = {
    code: region
    for region, codes in _REGIONS.items()
    for code in codes.split()
}


def region_for(country_code):
    return COUNTRY_TO_REGION.get((country_code or "").upper(), "other")
