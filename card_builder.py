from models import CardRecommendation, Market


def build_market_only_card(markets: list[Market]) -> list[CardRecommendation]:
    recommendations: list[CardRecommendation] = []

    for market in markets:
        recommendations.append(
            CardRecommendation(
                ticker=market.ticker,
                player=market.player,
                threshold=market.threshold,
                price_cents=market.yes_ask_cents,
                decision="PASS",
                reason=[
                    "Live market collected successfully.",
                    "No independent projection is connected yet.",
                    "A BUY recommendation is prohibited until projection and QC are available.",
                ],
            )
        )

    return recommendations
