import copy
from enum import Enum, auto
from random import uniform

from millify import millify

from TwitchChannelPointsMiner.utils import float_round


class Strategy(Enum):
    MOST_VOTED = auto()
    HIGH_ODDS = auto()
    PERCENTAGE = auto()
    SMART_MONEY = auto()
    SMART = auto()
    HISTORICAL = auto()
    KELLY_CRITERION = auto()
    CONTRARIAN = auto()
    MOMENTUM = auto()
    VALUE_BET = auto()
    WEIGHTED_AVERAGE = auto()
    UNDERDOG = auto()
    NUMBER_1 = auto()
    NUMBER_2 = auto()
    NUMBER_3 = auto()
    NUMBER_4 = auto()
    NUMBER_5 = auto()
    NUMBER_6 = auto()
    NUMBER_7 = auto()
    NUMBER_8 = auto()

    def __str__(self):
        return self.name


class DryRunResult(object):
    __slots__ = [
        "strategy_name",
        "choice",
        "amount",
        "outcome_title",
        "outcome_color",
        "result_type",
        "points_gained",
    ]

    def __init__(
        self,
        strategy_name,
        choice,
        amount,
        outcome_title="",
        outcome_color="",
    ):
        self.strategy_name = strategy_name
        self.choice = choice
        self.amount = amount
        self.outcome_title = outcome_title
        self.outcome_color = outcome_color
        self.result_type = None
        self.points_gained = 0

    def __repr__(self):
        return (
            f"DryRunResult(strategy={self.strategy_name}, "
            f"choice={self.choice}, amount={self.amount}, "
            f"outcome={self.outcome_title})"
        )

    def to_dict(self):
        return {
            "strategy": self.strategy_name,
            "choice": self.choice,
            "amount": self.amount,
            "outcome_title": self.outcome_title,
            "outcome_color": self.outcome_color,
            "result_type": self.result_type,
            "points_gained": self.points_gained,
        }


class Condition(Enum):
    GT = auto()
    LT = auto()
    GTE = auto()
    LTE = auto()

    def __str__(self):
        return self.name


class OutcomeKeys(object):
    # Real key on Bet dict ['']
    PERCENTAGE_USERS = "percentage_users"
    ODDS_PERCENTAGE = "odds_percentage"
    ODDS = "odds"
    TOP_POINTS = "top_points"
    # Real key on Bet dict [''] - Sum()
    TOTAL_USERS = "total_users"
    TOTAL_POINTS = "total_points"
    # This key does not exist
    DECISION_USERS = "decision_users"
    DECISION_POINTS = "decision_points"


class DelayMode(Enum):
    FROM_START = auto()
    FROM_END = auto()
    PERCENTAGE = auto()

    def __str__(self):
        return self.name


class FilterCondition(object):
    __slots__ = [
        "by",
        "where",
        "value",
    ]

    def __init__(self, by=None, where=None, value=None, decision=None):
        self.by = by
        self.where = where
        self.value = value

    def __repr__(self):
        return f"FilterCondition(by={self.by.upper()}, where={self.where}, value={self.value})"


class BetSettings(object):
    __slots__ = [
        "strategy",
        "percentage",
        "percentage_gap",
        "max_points",
        "minimum_points",
        "stealth_mode",
        "filter_condition",
        "delay",
        "delay_mode",
        "historical_outcomes",
    ]

    def __init__(
        self,
        strategy: Strategy = None,
        percentage: int = None,
        percentage_gap: int = None,
        max_points: int = None,
        minimum_points: int = None,
        stealth_mode: bool = None,
        filter_condition: FilterCondition = None,
        delay: float = None,
        delay_mode: DelayMode = None,
        historical_outcomes: list = None,
    ):
        self.strategy = strategy
        self.percentage = percentage
        self.percentage_gap = percentage_gap
        self.max_points = max_points
        self.minimum_points = minimum_points
        self.stealth_mode = stealth_mode
        self.filter_condition = filter_condition
        self.delay = delay
        self.delay_mode = delay_mode
        self.historical_outcomes = historical_outcomes if historical_outcomes is not None else []

    def default(self):
        self.strategy = self.strategy if self.strategy is not None else Strategy.SMART
        self.percentage = self.percentage if self.percentage is not None else 5
        self.percentage_gap = (
            self.percentage_gap if self.percentage_gap is not None else 20
        )
        self.max_points = self.max_points if self.max_points is not None else 50000
        self.minimum_points = (
            self.minimum_points if self.minimum_points is not None else 0
        )
        self.stealth_mode = (
            self.stealth_mode if self.stealth_mode is not None else False
        )
        self.delay = self.delay if self.delay is not None else 6
        self.delay_mode = (
            self.delay_mode if self.delay_mode is not None else DelayMode.FROM_END
        )
        if self.historical_outcomes is None:
            self.historical_outcomes = []

    def __repr__(self):
        return f"BetSettings(strategy={self.strategy}, percentage={self.percentage}, percentage_gap={self.percentage_gap}, max_points={self.max_points}, minimum_points={self.minimum_points}, stealth_mode={self.stealth_mode})"


class Bet(object):
    __slots__ = ["outcomes", "decision", "total_users", "total_points", "settings"]

    def __init__(self, outcomes: list, settings: BetSettings):
        self.outcomes = outcomes
        self.__clear_outcomes()
        self.decision: dict = {}
        self.total_users = 0
        self.total_points = 0
        self.settings = settings

    def update_outcomes(self, outcomes):
        for index in range(0, len(self.outcomes)):
            self.outcomes[index][OutcomeKeys.TOTAL_USERS] = int(
                outcomes[index][OutcomeKeys.TOTAL_USERS]
            )
            self.outcomes[index][OutcomeKeys.TOTAL_POINTS] = int(
                outcomes[index][OutcomeKeys.TOTAL_POINTS]
            )
            if outcomes[index]["top_predictors"] != []:
                # Sort by points placed by other users
                outcomes[index]["top_predictors"] = sorted(
                    outcomes[index]["top_predictors"],
                    key=lambda x: x["points"],
                    reverse=True,
                )
                # Get the first elements (most placed)
                top_points = outcomes[index]["top_predictors"][0]["points"]
                self.outcomes[index][OutcomeKeys.TOP_POINTS] = top_points

        # Inefficient, but otherwise outcomekeys are represented wrong
        self.total_points = 0
        self.total_users = 0
        for index in range(0, len(self.outcomes)):
            self.total_users += self.outcomes[index][OutcomeKeys.TOTAL_USERS]
            self.total_points += self.outcomes[index][OutcomeKeys.TOTAL_POINTS]

        if (
            self.total_users > 0
            and self.total_points > 0
        ):
            for index in range(0, len(self.outcomes)):
                self.outcomes[index][OutcomeKeys.PERCENTAGE_USERS] = float_round(
                    (100 * self.outcomes[index][OutcomeKeys.TOTAL_USERS]) / self.total_users
                )
                self.outcomes[index][OutcomeKeys.ODDS] = float_round(
                    0
                    if self.outcomes[index][OutcomeKeys.TOTAL_POINTS] == 0
                    else self.total_points / self.outcomes[index][OutcomeKeys.TOTAL_POINTS]
                )
                self.outcomes[index][OutcomeKeys.ODDS_PERCENTAGE] = float_round(
                    0
                    if self.outcomes[index][OutcomeKeys.ODDS] == 0
                    else 100 / self.outcomes[index][OutcomeKeys.ODDS]
                )

        self.__clear_outcomes()

    def __repr__(self):
        return f"Bet(total_users={millify(self.total_users)}, total_points={millify(self.total_points)}), decision={self.decision})\n\t\tOutcome A({self.get_outcome(0)})\n\t\tOutcome B({self.get_outcome(1)})"

    def get_decision(self, parsed=False):
        decision = self.outcomes[self.decision["choice"]]
        return decision if parsed is False else Bet.__parse_outcome(decision)

    @staticmethod
    def __parse_outcome(outcome):
        return f"{outcome['title']} ({outcome['color']}), Points: {millify(outcome[OutcomeKeys.TOTAL_POINTS])}, Users: {millify(outcome[OutcomeKeys.TOTAL_USERS])} ({outcome[OutcomeKeys.PERCENTAGE_USERS]}%), Odds: {outcome[OutcomeKeys.ODDS]} ({outcome[OutcomeKeys.ODDS_PERCENTAGE]}%)"

    def get_outcome(self, index):
        return Bet.__parse_outcome(self.outcomes[index])

    def __clear_outcomes(self):
        for index in range(0, len(self.outcomes)):
            keys = copy.deepcopy(list(self.outcomes[index].keys()))
            for key in keys:
                if key not in [
                    OutcomeKeys.TOTAL_USERS,
                    OutcomeKeys.TOTAL_POINTS,
                    OutcomeKeys.TOP_POINTS,
                    OutcomeKeys.PERCENTAGE_USERS,
                    OutcomeKeys.ODDS,
                    OutcomeKeys.ODDS_PERCENTAGE,
                    "title",
                    "color",
                    "id",
                ]:
                    del self.outcomes[index][key]
            for key in [
                OutcomeKeys.PERCENTAGE_USERS,
                OutcomeKeys.ODDS,
                OutcomeKeys.ODDS_PERCENTAGE,
                OutcomeKeys.TOP_POINTS,
            ]:
                if key not in self.outcomes[index]:
                    self.outcomes[index][key] = 0

    def __return_choice(self, key) -> int:
        largest=0
        for index in range(0, len(self.outcomes)):
            if self.outcomes[index][key] > self.outcomes[largest][key]:
                largest = index
        return largest

    def __return_number_choice(self, number) -> int:
        if (len(self.outcomes) > number):
            return number
        else:
            return 0

    def __historical_choice(self) -> int:
        """Choose outcome based on historical win rates, weighted with current odds.

        Uses dry_run_predictions from historical_outcomes (loaded from analytics JSON)
        to determine which outcome index historically performs best.
        Falls back to SMART strategy if no historical data is available.

        Also sets self.decision["confidence"] (0.0–1.0) based on:
          - sample_size: more data → higher confidence (sigmoid-like curve)
          - win_rate_consistency: less variance across history → higher confidence
          - score_margin: bigger gap between best and 2nd best → higher confidence
        """
        history = getattr(self.settings, "historical_outcomes", [])
        if not history:
            self.decision["confidence"] = 0.0
            # Fall back to SMART strategy logic
            difference = abs(
                self.outcomes[0][OutcomeKeys.PERCENTAGE_USERS]
                - self.outcomes[1][OutcomeKeys.PERCENTAGE_USERS]
            )
            return (
                self.__return_choice(OutcomeKeys.ODDS)
                if difference < self.settings.percentage_gap
                else self.__return_choice(OutcomeKeys.TOTAL_USERS)
            )

        # Count historical wins per outcome index
        num_outcomes = len(self.outcomes)
        win_counts = [0] * num_outcomes
        loss_counts = [0] * num_outcomes
        total_points_delta = [0] * num_outcomes

        for pred in history:
            strategies = pred.get("strategies", [])
            for s in strategies:
                # Use the active strategy's historical result
                if s.get("strategy") == pred.get("active_strategy"):
                    idx = s.get("choice", 0)
                    if 0 <= idx < num_outcomes:
                        if s.get("result_type") == "WIN":
                            win_counts[idx] += 1
                        elif s.get("result_type") == "LOSE":
                            loss_counts[idx] += 1
                        total_points_delta[idx] += s.get("points_gained", 0)

        # Calculate a weighted score per outcome:
        # historical_win_rate * 0.6 + current_odds_advantage * 0.4
        scores = []
        for i in range(num_outcomes):
            total = win_counts[i] + loss_counts[i]
            if total > 0:
                win_rate = win_counts[i] / total
            else:
                win_rate = 0.5  # No data: neutral

            odds_pct = self.outcomes[i].get(OutcomeKeys.ODDS_PERCENTAGE, 50)
            odds_score = odds_pct / 100.0

            # Weighted combination: historical performance + current market sentiment
            combined = (win_rate * 0.6) + (odds_score * 0.4)
            scores.append(combined)

        # Return the index with the highest combined score
        best = 0
        for i in range(1, num_outcomes):
            if scores[i] > scores[best]:
                best = i

        # --- Confidence scoring ---
        total_samples = sum(win_counts) + sum(loss_counts)
        # Sample size factor: sigmoid curve – 10 samples ≈ 0.5, 30+ ≈ 0.9
        sample_conf = 1.0 - (1.0 / (1.0 + total_samples / 10.0))
        # Score margin: how far best is ahead of runner-up
        sorted_scores = sorted(scores, reverse=True)
        if len(sorted_scores) >= 2 and sorted_scores[0] > 0:
            margin_conf = min((sorted_scores[0] - sorted_scores[1]) / sorted_scores[0], 1.0)
        else:
            margin_conf = 0.0
        # Win-rate consistency: best outcome's raw win rate
        best_total = win_counts[best] + loss_counts[best]
        consistency_conf = (win_counts[best] / best_total) if best_total > 0 else 0.5

        self.decision["confidence"] = round(
            sample_conf * 0.4 + margin_conf * 0.3 + consistency_conf * 0.3, 3
        )

        return best

    def __kelly_choice(self) -> int:
        """Kelly Criterion: choose the outcome that maximises expected geometric
        growth.  Kelly fraction = (bp - q) / b  where b = odds-1, p = implied
        probability from voter %, q = 1 - p.  Pick the outcome with the highest
        positive Kelly fraction (meaning the market thinks it's the best value)."""
        num = len(self.outcomes)
        best = 0
        best_kelly = -1.0
        for i in range(num):
            odds = self.outcomes[i].get(OutcomeKeys.ODDS, 1)
            if odds <= 1:
                continue
            b = odds - 1.0
            p = self.outcomes[i].get(OutcomeKeys.PERCENTAGE_USERS, 50) / 100.0
            q = 1.0 - p
            kelly = (b * p - q) / b if b > 0 else 0
            if kelly > best_kelly:
                best_kelly = kelly
                best = i
        return best

    def __contrarian_choice(self) -> int:
        """Contrarian: bet against the crowd.  Pick the least-voted outcome,
        which by definition offers the highest payout odds."""
        least = 0
        for i in range(1, len(self.outcomes)):
            if self.outcomes[i][OutcomeKeys.TOTAL_USERS] < self.outcomes[least][OutcomeKeys.TOTAL_USERS]:
                least = i
        return least

    def __momentum_choice(self) -> int:
        """Momentum: combine voter count momentum with points momentum.
        The outcome attracting the most *points per voter* is trending hardest
        among informed bettors (those putting real points behind it)."""
        best = 0
        best_ratio = 0.0
        for i in range(len(self.outcomes)):
            users = self.outcomes[i].get(OutcomeKeys.TOTAL_USERS, 0)
            points = self.outcomes[i].get(OutcomeKeys.TOTAL_POINTS, 0)
            ratio = points / max(users, 1)
            if ratio > best_ratio:
                best_ratio = ratio
                best = i
        return best

    def __value_bet_choice(self) -> int:
        """Value Bet: identify the outcome where the implied probability from
        the voter split is significantly higher than the implied probability
        from the points split.  This means the crowd *believes* in it more
        than the smart money, suggesting value."""
        best = 0
        best_diff = -999.0
        for i in range(len(self.outcomes)):
            user_pct = self.outcomes[i].get(OutcomeKeys.PERCENTAGE_USERS, 0)
            odds_pct = self.outcomes[i].get(OutcomeKeys.ODDS_PERCENTAGE, 0)
            # Positive diff means voters like it more than the point-weighted odds suggest
            diff = user_pct - odds_pct
            if diff > best_diff:
                best_diff = diff
                best = i
        return best

    def __weighted_average_choice(self) -> int:
        """Weighted Average: score each outcome with a balanced blend of all
        available signals — voter %, odds %, points-per-user ratio, and top-
        predictor conviction.  Normalise each to 0-1 and take maximum."""
        num = len(self.outcomes)
        scores = [0.0] * num

        # Normalisation helpers
        def _norm(values):
            mx = max(values) if values else 1
            return [(v / mx) if mx > 0 else 0 for v in values]

        user_pcts = [self.outcomes[i].get(OutcomeKeys.PERCENTAGE_USERS, 0) for i in range(num)]
        odds_pcts = [self.outcomes[i].get(OutcomeKeys.ODDS_PERCENTAGE, 0) for i in range(num)]
        ppu = [
            self.outcomes[i].get(OutcomeKeys.TOTAL_POINTS, 0) / max(self.outcomes[i].get(OutcomeKeys.TOTAL_USERS, 1), 1)
            for i in range(num)
        ]
        tops = [self.outcomes[i].get(OutcomeKeys.TOP_POINTS, 0) for i in range(num)]

        n_user = _norm(user_pcts)
        n_odds = _norm(odds_pcts)
        n_ppu = _norm(ppu)
        n_top = _norm(tops)

        for i in range(num):
            scores[i] = 0.30 * n_user[i] + 0.25 * n_odds[i] + 0.25 * n_ppu[i] + 0.20 * n_top[i]

        best = 0
        for i in range(1, num):
            if scores[i] > scores[best]:
                best = i
        return best

    def __underdog_choice(self) -> int:
        """Underdog: always pick the outcome with the highest odds (lowest
        total points bet).  Similar to HIGH_ODDS but also considers that
        outcomes with very few points but decent voter counts often represent
        a mis-priced underdog.  Falls back to pure odds."""
        best = 0
        best_odds = 0.0
        for i in range(len(self.outcomes)):
            odds = self.outcomes[i].get(OutcomeKeys.ODDS, 0)
            users = self.outcomes[i].get(OutcomeKeys.TOTAL_USERS, 0)
            # Boost odds score when a reasonable number of users also picked it
            # This avoids outcomes with 0 users and infinite implied odds
            boost = min(users / max(self.total_users, 1), 1.0) if self.total_users > 0 else 0.5
            score = odds * (0.7 + 0.3 * boost)
            if score > best_odds:
                best_odds = score
                best = i
        return best

    def skip(self) -> tuple:
        if self.settings.filter_condition is not None:
            # key == by , condition == where
            key = self.settings.filter_condition.by
            condition = self.settings.filter_condition.where
            value = self.settings.filter_condition.value

            fixed_key = (
                key
                if key not in [OutcomeKeys.DECISION_USERS, OutcomeKeys.DECISION_POINTS]
                else key.replace("decision", "total")
            )
            if key in [OutcomeKeys.TOTAL_USERS, OutcomeKeys.TOTAL_POINTS]:
                compared_value = (
                    self.outcomes[0][fixed_key] + self.outcomes[1][fixed_key]
                )
            else:
                outcome_index = self.decision["choice"]
                compared_value = self.outcomes[outcome_index][fixed_key]

            # Check if condition is satisfied
            if condition == Condition.GT:
                if compared_value > value:
                    return False, compared_value
            elif condition == Condition.LT:
                if compared_value < value:
                    return False, compared_value
            elif condition == Condition.GTE:
                if compared_value >= value:
                    return False, compared_value
            elif condition == Condition.LTE:
                if compared_value <= value:
                    return False, compared_value
            return True, compared_value  # Else skip the bet
        else:
            return False, 0  # Default don't skip the bet

    def calculate(self, balance: int) -> dict:
        self.decision = {"choice": None, "amount": 0, "id": None}
        if self.settings.strategy == Strategy.MOST_VOTED:
            self.decision["choice"] = self.__return_choice(OutcomeKeys.TOTAL_USERS)
        elif self.settings.strategy == Strategy.HIGH_ODDS:
            self.decision["choice"] = self.__return_choice(OutcomeKeys.ODDS)
        elif self.settings.strategy == Strategy.PERCENTAGE:
            self.decision["choice"] = self.__return_choice(OutcomeKeys.ODDS_PERCENTAGE)
        elif self.settings.strategy == Strategy.SMART_MONEY:
            self.decision["choice"] = self.__return_choice(OutcomeKeys.TOP_POINTS)
        elif self.settings.strategy == Strategy.NUMBER_1:
            self.decision["choice"] = self.__return_number_choice(0)
        elif self.settings.strategy == Strategy.NUMBER_2:
            self.decision["choice"] = self.__return_number_choice(1)
        elif self.settings.strategy == Strategy.NUMBER_3:
            self.decision["choice"] = self.__return_number_choice(2)
        elif self.settings.strategy == Strategy.NUMBER_4:
            self.decision["choice"] = self.__return_number_choice(3)
        elif self.settings.strategy == Strategy.NUMBER_5:
            self.decision["choice"] = self.__return_number_choice(4)
        elif self.settings.strategy == Strategy.NUMBER_6:
            self.decision["choice"] = self.__return_number_choice(5)
        elif self.settings.strategy == Strategy.NUMBER_7:
            self.decision["choice"] = self.__return_number_choice(6)
        elif self.settings.strategy == Strategy.NUMBER_8:
            self.decision["choice"] = self.__return_number_choice(7)
        elif self.settings.strategy == Strategy.SMART:
            difference = abs(
                self.outcomes[0][OutcomeKeys.PERCENTAGE_USERS]
                - self.outcomes[1][OutcomeKeys.PERCENTAGE_USERS]
            )
            self.decision["choice"] = (
                self.__return_choice(OutcomeKeys.ODDS)
                if difference < self.settings.percentage_gap
                else self.__return_choice(OutcomeKeys.TOTAL_USERS)
            )
        elif self.settings.strategy == Strategy.HISTORICAL:
            self.decision["choice"] = self.__historical_choice()
        elif self.settings.strategy == Strategy.KELLY_CRITERION:
            self.decision["choice"] = self.__kelly_choice()
        elif self.settings.strategy == Strategy.CONTRARIAN:
            self.decision["choice"] = self.__contrarian_choice()
        elif self.settings.strategy == Strategy.MOMENTUM:
            self.decision["choice"] = self.__momentum_choice()
        elif self.settings.strategy == Strategy.VALUE_BET:
            self.decision["choice"] = self.__value_bet_choice()
        elif self.settings.strategy == Strategy.WEIGHTED_AVERAGE:
            self.decision["choice"] = self.__weighted_average_choice()
        elif self.settings.strategy == Strategy.UNDERDOG:
            self.decision["choice"] = self.__underdog_choice()

        if self.decision["choice"] is not None:
            index = self.decision["choice"]
            self.decision["id"] = self.outcomes[index]["id"]
            self.decision["amount"] = min(
                int(balance * (self.settings.percentage / 100)),
                self.settings.max_points,
            )
            if (
                self.settings.stealth_mode is True
                and self.decision["amount"]
                >= self.outcomes[index][OutcomeKeys.TOP_POINTS]
            ):
                reduce_amount = uniform(1, 5)
                self.decision["amount"] = (
                    self.outcomes[index][OutcomeKeys.TOP_POINTS] - reduce_amount
                )
            self.decision["amount"] = int(self.decision["amount"])
        return self.decision

    def dry_run_all_strategies(self, balance):
        results = []
        # Skip NUMBER_X strategies that reference non-existent outcomes
        num_outcomes = len(self.outcomes)
        number_strategies = {
            Strategy.NUMBER_1: 0,
            Strategy.NUMBER_2: 1,
            Strategy.NUMBER_3: 2,
            Strategy.NUMBER_4: 3,
            Strategy.NUMBER_5: 4,
            Strategy.NUMBER_6: 5,
            Strategy.NUMBER_7: 6,
            Strategy.NUMBER_8: 7,
        }

        # Save original decision so we can restore it
        original_decision = dict(self.decision) if self.decision else {}
        original_strategy = self.settings.strategy

        for strategy in Strategy:
            # Skip NUMBER_X strategies that exceed available outcomes
            if strategy in number_strategies:
                if number_strategies[strategy] >= num_outcomes:
                    continue

            self.settings.strategy = strategy
            self.decision = {"choice": None, "amount": 0, "id": None}
            self.calculate(balance)

            if self.decision["choice"] is not None:
                idx = self.decision["choice"]
                result = DryRunResult(
                    strategy_name=str(strategy),
                    choice=idx,
                    amount=self.decision["amount"],
                    outcome_title=self.outcomes[idx].get("title", ""),
                    outcome_color=self.outcomes[idx].get("color", ""),
                )
                results.append(result)

        # Restore original state
        self.settings.strategy = original_strategy
        self.decision = original_decision

        return results
