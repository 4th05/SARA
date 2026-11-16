"""Speak only requested actions and outcomes confirmed by controller feedback."""
from gateway.contracts import Action, ROOMS

NAMES = {
    "en": {"bathroom": "the bathroom light", "kitchen": "the kitchen light", "bedroom": "the bedroom light",
           "lights": "all three lights", "pump": "the water pump"},
    "pt": {"bathroom": "luz do banheiro", "kitchen": "luz da cozinha", "bedroom": "luz do quarto",
           "lights": "todas as três luzes", "pump": "bomba de água"},
}


def acknowledge(actions, language="en"):
    rooms = {action.device for action in actions}
    values = {action.value for action in actions}
    if rooms == set(ROOMS) and len(values) == 1:
        actions = [Action(device="lights", value=actions[0].value)]
    requests = []
    for action in actions:
        name = NAMES[language][action.device]
        if language == "pt":
            requests.append(f"{'ligar' if action.value else 'desligar'} {name}")
        else:
            requests.append(f"turn {name} {'on' if action.value else 'off'}")
    if language == "pt":
        return "Vou " + " e ".join(requests) + ". Aguardando confirmação do controlador."
    return "I'll " + " and ".join(requests) + ". Waiting for the controller to confirm."


def describe_feedback(feedback, language="en"):
    name = NAMES[language][feedback.device]
    if feedback.outcome == "warning" and feedback.reported_value is None:
        if language == "pt":
            return f"O controlador informou um aviso para {name}. O estado solicitado continua sem confirmação."
        return f"The controller reported a warning for {name}. The requested state remains unconfirmed."
    if language == "pt":
        if feedback.outcome in ("success", "warning"):
            state = "ligada" if feedback.reported_value else "desligada"
            if feedback.device == "lights":
                state += "s"
            result = f"O controlador confirmou {name} {state}."
            if feedback.outcome == "warning":
                result += " Há um aviso de alimentação no controlador simulado."
            return result
        if feedback.outcome == "timeout":
            return f"Não recebi confirmação de {name}."
        return f"O controlador rejeitou o comando para {name}."
    if feedback.outcome in ("success", "warning"):
        result = f"The controller confirmed {name} {'on' if feedback.reported_value else 'off'}."
        if feedback.outcome == "warning":
            result += " The simulated controller reported a supply warning."
        return result
    if feedback.outcome == "timeout":
        return f"No confirmation arrived for {name}."
    return f"The controller rejected the command for {name}."
