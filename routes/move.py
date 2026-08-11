import os
from collections import defaultdict
from flask import jsonify, request



# ============================================================
# Opponent analysis
# ============================================================

def analyze_opponent(recent_hands, opponent_seat):
    """
    Build a lightweight profile of the opponent from recent hands.

    recent_hands contains up to the last 20 completed hands.
    """

    stats = {
        "hands": 0,
        "pre_raises": 0,
        "post_bets": 0,
        "post_raises": 0,
        "folds": 0,
        "calls": 0,
        "showdowns": 0,
        "weak_showdowns": 0,
        "strong_showdowns": 0,
    }

    for hand in recent_hands:
        actions = hand.get("actions", [])

        opponent_acted = False

        for action in actions:
            if action.get("seat") != opponent_seat:
                continue

            opponent_acted = True
            action_type = action.get("action")
            round_name = action.get("round")

            if action_type == "raise":
                if round_name == "pre_reveal":
                    stats["pre_raises"] += 1
                else:
                    stats["post_raises"] += 1

            elif action_type == "bet":
                if round_name == "post_reveal":
                    stats["post_bets"] += 1

            elif action_type == "fold":
                stats["folds"] += 1

            elif action_type == "call":
                stats["calls"] += 1

        if opponent_acted:
            stats["hands"] += 1

        # If opponent reached showdown, inspect their number.
        shown_numbers = hand.get("shown_numbers", {})
        if str(opponent_seat) in shown_numbers:
            stats["showdowns"] += 1

            number = shown_numbers[str(opponent_seat)]
            community = hand.get("community_number")

            if community is not None:
                if number == community:
                    stats["strong_showdowns"] += 1
                elif number >= 10:
                    stats["strong_showdowns"] += 1
                elif number <= 5:
                    stats["weak_showdowns"] += 1

    # Derived tendencies
    total_aggression = (
        stats["pre_raises"]
        + stats["post_bets"]
        + stats["post_raises"]
    )

    stats["aggressive"] = total_aggression >= 4
    stats["very_aggressive"] = total_aggression >= 7

    stats["passive"] = (
        stats["calls"] >= 4
        and total_aggression <= 2
    )

    stats["bluffy"] = (
        stats["showdowns"] >= 3
        and stats["weak_showdowns"] >= 2
    )

    return stats


# ============================================================
# Hand evaluation
# ============================================================

def is_pair(your_number, community_number, table_rule="standard"):
    if table_rule == "wild_seven":
        return your_number == 7 or your_number == community_number

    return your_number == community_number


def evaluate_strength(your_number, community_number, table_rule="standard"):
    """
    Return a numerical strength score for showdown evaluation.

    The active table rule changes what counts as a strong hand.
    """

    if community_number is not None and is_pair(
        your_number,
        community_number,
        table_rule
    ):
        if table_rule == "low_ball":
            return -your_number

        if table_rule == "pair_bounty":
            return 105 + your_number

        if table_rule == "wild_seven":
            pair_value = community_number
            if your_number == 7 and community_number != 7:
                pair_value = 7
            return 100 + pair_value

        return 100 + your_number

    if table_rule == "low_ball":
        return 100 - your_number

    return your_number


def get_strategy_strength(your_number, table_rule="standard"):
    """
    Map a hand to the same rough strength buckets used by the strategy
    logic, while respecting the active table rule.
    """

    if table_rule == "low_ball":
        return 14 - your_number

    return your_number


def estimate_pre_reveal_equity(your_number, table_rule="standard"):
    """
    Rough estimate of how often our number beats a random
    opponent number before seeing the community number.

    Pair possibilities are also considered.

    This doesn't attempt to model the opponent's exact range.
    """

    # Enumerate all possible opponent numbers and community numbers.
    wins = 0
    total = 0

    for opponent_number in range(1, 14):
        for community in range(1, 14):
            my_strength = evaluate_strength(
                your_number,
                community,
                table_rule
            )
            opponent_strength = evaluate_strength(
                opponent_number,
                community,
                table_rule
            )

            total += 1

            if my_strength > opponent_strength:
                wins += 1
            elif my_strength == opponent_strength:
                wins += 0.5

    return wins / total


def estimate_post_reveal_equity(
    your_number,
    community_number,
    table_rule="standard"
):
    """
    After reveal, opponent's number is still uniformly distributed
    from 1-13.

    Calculate exact showdown equity against that distribution.
    """

    wins = 0
    total = 13

    my_strength = evaluate_strength(
        your_number,
        community_number,
        table_rule
    )

    for opponent_number in range(1, 14):
        opponent_strength = evaluate_strength(
            opponent_number,
            community_number,
            table_rule
        )

        if my_strength > opponent_strength:
            wins += 1
        elif my_strength == opponent_strength:
            wins += 0.5

    return wins / total


# ============================================================
# Pot odds
# ============================================================

def calculate_required_equity(pot, to_call):
    """
    Approximate minimum equity required to call.

    Example:
        pot = 30
        to_call = 10

        required equity = 10 / 40 = 25%
    """

    if to_call <= 0:
        return 0.0

    return to_call / (pot + to_call)


# ============================================================
# Betting helpers
# ============================================================

def legal_action(actions, desired):
    """
    Make sure we never return an illegal action.
    """

    if desired in actions:
        return desired

    # Safe fallbacks
    if "check" in actions:
        return "check"

    if "call" in actions:
        return "call"

    if "fold" in actions:
        return "fold"

    return actions[0]


def choose_raise_amount(state, strength, bluff=False):
    """
    Choose a legal raise/bet amount.

    The API expects the TOTAL amount invested during this
    betting round, not the amount added.
    """

    min_raise = state.get("min_raise_to")
    max_raise = state.get("max_raise_to")

    if min_raise is None or max_raise is None:
        return None

    if min_raise >= max_raise:
        return min_raise

    pot = state.get("pot", 0)

    if bluff:
        # Smaller pressure bet.
        target = int(pot * 0.50)

    elif strength >= 113:
        # Pair: apply serious pressure.
        target = int(pot * 1.00)

    elif strength >= 10:
        # Strong high number.
        target = int(pot * 0.75)

    else:
        target = int(pot * 0.50)

    target = max(target, min_raise)
    target = min(target, max_raise)

    return target


# ============================================================
# Pre-reveal strategy
# ============================================================

def decide_pre_reveal(state, opponent):
    your_number = state["your_number"]
    pot = state["pot"]
    to_call = state["to_call"]
    actions = state["legal_actions"]
    table_rule = state.get("table_rule", "standard") or "standard"

    equity = estimate_pre_reveal_equity(your_number, table_rule)
    required_equity = calculate_required_equity(pot, to_call)
    strategy_strength = get_strategy_strength(your_number, table_rule)

    # --------------------------------------------------------
    # Very strong numbers
    # --------------------------------------------------------

    if strategy_strength >= 12:

        if "raise" in actions:
            amount = choose_raise_amount(
                state,
                your_number
            )

            return {
                "action": "raise",
                "amount": amount
            }

        if "bet" in actions:
            amount = choose_raise_amount(
                state,
                your_number
            )

            return {
                "action": "bet",
                "amount": amount
            }

        if "call" in actions:
            return {"action": "call"}

        return {"action": legal_action(actions, "check")}

    # --------------------------------------------------------
    # Medium numbers
    # --------------------------------------------------------

    if strategy_strength >= 8:

        # If the opponent is very aggressive, don't blindly
        # fight every pot.
        if opponent["very_aggressive"] and to_call > 0:
            if equity >= required_equity * 1.25:
                return {"action": legal_action(actions, "call")}

            return {
                "action": legal_action(
                    actions,
                    "fold"
                )
            }

        if to_call == 0:
            if "bet" in actions and equity >= 0.60:
                amount = choose_raise_amount(
                    state,
                    your_number
                )

                return {
                    "action": "bet",
                    "amount": amount
                }

            return {
                "action": legal_action(actions, "check")
            }

        if equity >= required_equity:
            return {
                "action": legal_action(actions, "call")
            }

        return {
            "action": legal_action(actions, "fold")
        }

    # --------------------------------------------------------
    # Weak numbers
    # --------------------------------------------------------

    if to_call > 0:

        # Don't pay large bets with weak cards.
        if required_equity > equity * 1.10:
            return {
                "action": legal_action(actions, "fold")
            }

        return {
            "action": legal_action(actions, "call")
        }

    # --------------------------------------------------------
    # Nobody has bet — opportunity to bluff
    # --------------------------------------------------------

    if (
        "bet" in actions
        and opponent["passive"]
        and strategy_strength <= 5
    ):
        amount = choose_raise_amount(
            state,
            your_number,
            bluff=True
        )

        return {
            "action": "bet",
            "amount": amount
        }

    return {
        "action": legal_action(actions, "check")
    }


# ============================================================
# Post-reveal strategy
# ============================================================

def decide_post_reveal(state, opponent):

    your_number = state["your_number"]
    community = state["community_number"]

    pot = state["pot"]
    to_call = state["to_call"]
    actions = state["legal_actions"]
    table_rule = state.get("table_rule", "standard") or "standard"

    strength = evaluate_strength(
        your_number,
        community,
        table_rule
    )
    strategy_strength = get_strategy_strength(your_number, table_rule)
    sizing_strength = evaluate_strength(
        your_number,
        community,
        "standard"
    )

    equity = estimate_post_reveal_equity(
        your_number,
        community,
        table_rule
    )

    required_equity = calculate_required_equity(
        pot,
        to_call
    )

    # --------------------------------------------------------
    # PAIR
    # --------------------------------------------------------

    if table_rule != "low_ball" and is_pair(your_number, community, table_rule):

        if "raise" in actions:

            amount = choose_raise_amount(
                state,
                sizing_strength
            )

            return {
                "action": "raise",
                "amount": amount
            }

        if "bet" in actions:

            amount = choose_raise_amount(
                state,
                sizing_strength
            )

            return {
                "action": "bet",
                "amount": amount
            }

        if "call" in actions:
            return {"action": "call"}

        return {
            "action": legal_action(actions, "check")
        }

    # --------------------------------------------------------
    # Very strong non-pair: 13 / 12
    # --------------------------------------------------------

    if strategy_strength >= 12:

        if to_call > 0:

            # Strong enough to continue against most bets.
            if equity >= required_equity:

                if (
                    "raise" in actions
                    and opponent["passive"]
                ):
                    amount = choose_raise_amount(
                        state,
                        sizing_strength
                    )

                    return {
                        "action": "raise",
                        "amount": amount
                    }

                return {
                    "action": legal_action(
                        actions,
                        "call"
                    )
                }

            return {
                "action": legal_action(actions, "fold")
            }

        if "bet" in actions:

            amount = choose_raise_amount(
                state,
                sizing_strength
            )

            return {
                "action": "bet",
                "amount": amount
            }

        return {
            "action": legal_action(actions, "check")
        }

    # --------------------------------------------------------
    # Medium/strong non-pair
    # --------------------------------------------------------

    if strategy_strength >= 8:

        if to_call > 0:

            if equity >= required_equity * 1.10:

                return {
                    "action": legal_action(
                        actions,
                        "call"
                    )
                }

            # Against a bluffy opponent, give them
            # a little more credit for aggression.
            if opponent["bluffy"] and equity >= required_equity:

                return {
                    "action": legal_action(
                        actions,
                        "call"
                    )
                }

            return {
                "action": legal_action(actions, "fold")
            }

        # Nobody has bet.
        if "bet" in actions and equity >= 0.55:

            amount = choose_raise_amount(
                state,
                sizing_strength
            )

            return {
                "action": "bet",
                "amount": amount
            }

        return {
            "action": legal_action(actions, "check")
        }

    # --------------------------------------------------------
    # Weak hand
    # --------------------------------------------------------

    if to_call == 0:

        # Potential bluff.
        if (
            "bet" in actions
            and opponent["passive"]
        ):
            amount = choose_raise_amount(
                state,
                your_number,
                bluff=True
            )

            return {
                "action": "bet",
                "amount": amount
            }

        return {
            "action": legal_action(actions, "check")
        }

    # Facing a bet with a weak hand.
    # Only bluff-catch when we have a clear edge over pot odds.
    if opponent["bluffy"] and equity >= required_equity * 1.15:

        return {
            "action": legal_action(actions, "call")
        }

    return {
        "action": legal_action(actions, "fold")
    }


# ============================================================
# Main decision engine
# ============================================================

def decide_move(state):

    your_seat = state["your_seat"]

    # Find opponent.
    opponent_seat = None

    for player in state["players"]:
        if player["seat"] != your_seat:
            opponent_seat = player["seat"]
            break

    opponent = analyze_opponent(
        state.get("recent_hands", []),
        opponent_seat
    )

    # --------------------------------------------------------
    # Never return an illegal action.
    # --------------------------------------------------------

    legal_actions = state.get("legal_actions", [])

    if not legal_actions:
        return {"action": "check"}

    # --------------------------------------------------------
    # If we're almost busted, become more conservative.
    # --------------------------------------------------------

    stack = state["your_stack"]
    table_rule = state.get("table_rule", "standard") or "standard"

    if stack <= 15:

        # Only continue with very strong hands.
        your_number = state["your_number"]
        community = state["community_number"]

        if community is not None:
            has_pair = table_rule != "low_ball" and is_pair(
                your_number,
                community,
                table_rule
            )
            is_strong = get_strategy_strength(your_number, table_rule) >= 12

            if not has_pair and not is_strong:

                if state["to_call"] > 0:
                    return {
                        "action": legal_action(
                            legal_actions,
                            "fold"
                        )
                    }

                return {
                    "action": legal_action(
                        legal_actions,
                        "check"
                    )
                }

    # --------------------------------------------------------
    # Phase-specific strategy
    # --------------------------------------------------------

    if state["round"] == "pre_reveal":
        move = decide_pre_reveal(
            state,
            opponent
        )
    else:
        move = decide_post_reveal(
            state,
            opponent
        )

    # --------------------------------------------------------
    # Final safety validation
    # --------------------------------------------------------

    action = move["action"]

    if action not in legal_actions:
        action = legal_action(
            legal_actions,
            action
        )

    result = {
        "action": action
    }

    # Validate bet amount.
    if action in ("bet", "raise"):

        amount = move.get("amount")

        min_raise = state.get("min_raise_to")
        max_raise = state.get("max_raise_to")

        if (
            amount is None
            or min_raise is None
            or max_raise is None
            or amount < min_raise
            or amount > max_raise
        ):
            # Safer fallback.
            if "call" in legal_actions:
                return {"action": "call"}

            if "check" in legal_actions:
                return {"action": "check"}

            return {
                "action": legal_action(
                    legal_actions,
                    "fold"
                )
            }

        result["amount"] = int(amount)

    return result

def move():
    state = request.get_json()

    try:
        decision = decide_move(state)

        print(
            f"[HAND {state.get('hand_number')}] "
            f"round={state.get('round')} "
            f"number={state.get('your_number')} "
            f"community={state.get('community_number')} "
            f"pot={state.get('pot')} "
            f"to_call={state.get('to_call')} "
            f"decision={decision}"
        )

        return jsonify(decision), 200

    except Exception as e:
        # Never let an exception cause the endpoint to fail.
        print(f"ERROR: {e}")

        legal_actions = state.get(
            "legal_actions",
            ["check"]
        )

        if "check" in legal_actions:
            fallback = "check"
        elif "fold" in legal_actions:
            fallback = "fold"
        elif "call" in legal_actions:
            fallback = "call"
        else:
            fallback = legal_actions[0]

        return jsonify({
            "action": fallback
        }), 200