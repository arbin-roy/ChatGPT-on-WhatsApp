from boto3.dynamodb.conditions import Key
from fastapi.responses import JSONResponse
from fastapi import FastAPI, Form, Request
from twilio.rest import Client
from threading import Thread
from typing import Annotated
from mangum import Mangum
from boto3 import resource
import openai
import os

excReturnNumber = ''

app = FastAPI()
handler = Mangum(app)
ddb = resource("dynamodb")
testTable = ddb.Table("chat-db")
twilioClient = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
openai.api_key = os.getenv('OPENAI_API_KEY')


@app.exception_handler(Exception)
async def exception_handler(req: Request, exc: Exception):
    if "1600" in str(exc):
        send_msg(excReturnNumber, "Unable to create record: The concatenated message body exceeds the 1600 character "
                                  "limit.")
        return
    send_msg(excReturnNumber, exc)
    return JSONResponse(
        status_code=400,
        content={"message": f"{exc}"}
    )


@app.post("/twilioInboundWebhook")
def wappInbound(
        From: Annotated[str, Form()],
        Body: Annotated[str, Form()],
        ProfileName: Annotated[str, Form()],
        WaId: Annotated[str, Form()]
):
    global excReturnNumber
    excReturnNumber = From
    initiate_process(WaId, From, Body, ProfileName)


def get_completion(msl, model="gpt-3.5-turbo"):
    response = openai.ChatCompletion.create(
        model=model,
        messages=msl,
        temperature=0.3  # this is the degree of randomness of the model's output
    )
    return response.choices[0].message["content"]


def send_msg(to, body):
    twilioClient.messages.create(
        to=to,
        body=f"{body}",
        from_='whatsapp:+14155238886'
    )


def initiate_process(waId, to, body, pName):
    queryResponse = testTable.query(
        KeyConditionExpression=Key("wa-id").eq(waId)
    )
    if queryResponse["Count"] == 1:
        entireSet = queryResponse["Items"][0]["data"]
        total_calls = int(entireSet["user"]["totalCalls"])
        messages: list = entireSet["gpt"]["messages"]
        messageHistory: list = entireSet["gpt"]["messageHistory"]
        messageCount = int(entireSet["gpt"]["messageCounter"])

        if messageCount < 10:  # 10 interactions will be remembered
            messageCount += 1
            messages.append({"role": "user", "content": body})
        else:
            messageCount = 1
            messages.pop(0)  # Removing system message
            messageHistory.extend(messages)
            messages.clear()
            messages.extend(
                [
                    {"role": "system",
                     "content": "You act as an AI assistant who give responses in concise manner "
                                "and always under 1550 characters"},
                    {"role": "user", "content": body}
                ]
            )
        assistantResponse = get_completion(messages)
        messages.append({"role": "assistant", "content": assistantResponse})
        Thread(target=update_db, kwargs={
            "userType": "exi",
            "waId": waId,
            "total_calls": total_calls + 1,
            "messageCount": messageCount,
            "messages": messages,
            "messageHistory": messageHistory
        }).start()
        send_msg(to, assistantResponse)  # Can't run on thread, as it blocks outbound traffics.
        return
    elif queryResponse["Count"] == 0:
        msL = [
            {"role": "system",
             "content": "You act as an AI assistant who give responses in concise manner "
                        "and always under 1550 characters"},
            {"role": "user", "content": body}
        ]
        assistantResponse = get_completion(msL)
        msL.append({"role": "assistant", "content": assistantResponse})
        Thread(target=update_db, kwargs={
            "userType": "new",
            "waId": waId,
            "profileName": pName,
            "messages": msL,
        }).start()
        send_msg(to, assistantResponse)
        return
    else:
        print("Should not happen, but anyway!")


def update_db(userType, **kwargs):
    if userType == "exi":
        updateItem = testTable.update_item(
            Key={
                "wa-id": kwargs.get("waId")
            },
            UpdateExpression="SET #d.#u.#tC = :total_calls, #d.#g.#ms = :msList, #d.#g.#mc = :msCount, #d.#g.#msH ="
                             ":msHist",
            ExpressionAttributeNames={
                "#d": "data",
                "#u": "user",
                "#g": "gpt",
                "#tC": "totalCalls",
                "#ms": "messages",
                "#msH": "messageHistory",
                "#mc": "messageCounter"
            },
            ExpressionAttributeValues={
                ":total_calls": kwargs.get("total_calls"),
                ":msCount": kwargs.get("messageCount"),
                ":msList": kwargs.get("messages"),
                ":msHist": kwargs.get("messageHistory")
            },
            ReturnValues="NONE"
        )
        return updateItem
    elif userType == "new":
        data = {
            "user": {
                "profileName": kwargs.get("profileName"),
                "totalCalls": int(1)
            },
            "gpt": {
                "messageCounter": int(1),
                "messages": kwargs.get("messages"),
                "messageHistory": kwargs.get("messages")
            }
        }
        newItem = testTable.put_item(
            Item={
                "wa-id": kwargs.get("waId"),
                "data": data
            },
            ReturnValues="NONE"
        )
        return newItem
