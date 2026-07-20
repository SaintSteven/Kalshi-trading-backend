from app.models import CardRecommendation, Market

def build_market_only_card(markets: list[Market]) -> list[CardRecommendation]:
    return [CardRecommendation(
        ticker=m.ticker,
        player=m.player,
        threshold=m.threshold,
        price_cents=m.yes_ask_cents,
        decision="PASS",
        reason=[
            "Live market collected successfully.",
            "No independent projection is connected yet.",
            "A BUY recommendation is prohibited until projection and QC are available.",
        ],
    ) for m in markets]
