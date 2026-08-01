from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EzMoneyFundSpec:
    etf_code: str
    fund_code: str
    fund_name: str
    category: str

    @property
    def info_url(self) -> str:
        return f"https://www.ezmoney.com.tw/ETF/Fund/Info?FundCode={self.fund_code}#asset"


EZMONEY_INFO_FUNDS: tuple[EzMoneyFundSpec, ...] = (
    EzMoneyFundSpec(
        etf_code="00403A",
        fund_code="63YTW",
        fund_name="主動統一升級50",
        category="國內主動式",
    ),
    EzMoneyFundSpec(
        etf_code="00981A",
        fund_code="49YTW",
        fund_name="主動統一台股增長",
        category="國內主動式",
    ),
    EzMoneyFundSpec(
        etf_code="00988A",
        fund_code="61YTW",
        fund_name="主動統一全球創新",
        category="海外主動式",
    ),
)


def select_ezmoney_funds(etf_codes: list[str] | None = None) -> list[EzMoneyFundSpec]:
    if not etf_codes:
        return list(EZMONEY_INFO_FUNDS)

    requested = {code.upper() for code in etf_codes}
    selected = [fund for fund in EZMONEY_INFO_FUNDS if fund.etf_code.upper() in requested]
    missing = sorted(requested - {fund.etf_code.upper() for fund in selected})
    if missing:
        available = ", ".join(fund.etf_code for fund in EZMONEY_INFO_FUNDS)
        raise ValueError(f"EZMoney registry 找不到 ETF：{', '.join(missing)}；目前支援：{available}")
    return selected
