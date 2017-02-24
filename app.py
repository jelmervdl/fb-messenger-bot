import os
import sys
import json

import requests
from flask import Flask, request
from chat import State, link_osm, link_map

app = Flask(__name__)

class Conversation(object):
    def __init__(self):
        self.state = State()
        self.question = None

conversations = dict()

@app.route('/', methods=['GET'])
def verify():
    # when the endpoint is registered as a webhook, it must echo back
    # the 'hub.challenge' value it receives in the query arguments
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.challenge"):
        if not request.args.get("hub.verify_token") == os.environ["VERIFY_TOKEN"]:
            return "Verification token mismatch", 403
        return request.args["hub.challenge"], 200

    return "Hello world", 200


@app.route('/', methods=['POST'])
def webhook():

    # endpoint for processing incoming messaging events

    data = request.get_json()
    log(data)  # you may not want to log every incoming message in production, but it's good for testing

    if data["object"] == "page":

        for entry in data["entry"]:
            my_id = entry["id"]

            for messaging_event in entry["messaging"]:

                if messaging_event.get("message"):  # someone sent us a message

                    sender_id = messaging_event["sender"]["id"]        # the facebook ID of the person sending you the message
                    recipient_id = messaging_event["recipient"]["id"]  # the recipient's ID, which should be your page's facebook ID

                    # Ignore events to myself :P
                    if sender_id == my_id:
                        continue

                    if sender_id not in conversations:
                        conversations[sender_id] = Conversation()
                    
                    conversation = conversations[sender_id]
                    
                    if "text" in messaging_event.get("message"):
                        message_text = messaging_event["message"]["text"]

                        if conversation.question:
                            conversation.question.interpret(message_text, conversation.state)

                        if conversation.state.location:
                            send_message(sender_id, "Je bent hier: {}".format(conversation.state.location['display_name']))
                            send_message(sender_id, location=conversation.state.location)
                            del conversations[sender_id]
                        else:
                            conversation.question = conversation.state.next()
                            send_message(sender_id, conversation.question.text(conversation.state))

                if messaging_event.get("delivery"):  # delivery confirmation
                    pass

                if messaging_event.get("optin"):  # optin confirmation
                    pass

                if messaging_event.get("postback"):  # user clicked/tapped "postback" button in earlier message
                    pass

    return "ok", 200

def send_message(recipient_id, message_text=None, location=None):
    log("sending message to {recipient}: {text}".format(recipient=recipient_id, text=message_text))

    params = {
        "access_token": os.environ["PAGE_ACCESS_TOKEN"]
    }
    headers = {
        "Content-Type": "application/json"
    }
    message = dict()

    if message_text is not None:
        message['text'] = message_text

    elif location is not None:
        message['attachment'] = {
            'type': 'template',
            'payload': {
                'template_type': 'generic',
                'elements': [
                    {
                        'title': location['display_name'],
                        'image_url': link_map(location, (400, 300)),
                        'default_action': {
                            'type': 'web_url',
                            'url': link_osm(location)
                        },
                        'buttons': [
                            {
                                'type': 'web_url',
                                'url': link_osm(location),
                                'title': 'Open in OpenStreetMap'
                            }
                        ]
                    }
                ]
            }
        }

    data = json.dumps({
        "recipient": {
            "id": recipient_id
        },
        "message": message
    })
    r = requests.post("https://graph.facebook.com/v2.6/me/messages", params=params, headers=headers, data=data)
    if r.status_code != 200:
        log(data)
        log(r.status_code)
        log(r.text)

def log(message):  # simple wrapper for logging to stdout on heroku
    print(json.dumps(message))
    sys.stdout.flush()


if __name__ == '__main__':
    app.run(debug=True)
