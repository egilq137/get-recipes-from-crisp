import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List
from datetime import datetime
from models import Recipe

# current calendar period
calendar_year = datetime.today().year
week_number = datetime.today().isocalendar().week


# config
# email provider's SMTP server and credentials
# todo: this should go in another file
smtp_server = "smtp.gmail.com"
smtp_port = 587
sender_email = "egil137@gmail.com"
sender_password = "nwcw jmts yjxk nqmg "
recipients = ["egil137@gmail.com", "mladinic.vana@gmail.com"]


def add_text_attachment(msg):
    # read recipes according to the corresponding next week
    recipes_dir = Path("recipes") / f"{calendar_year}_Week_{week_number + 1}"

    for file in recipes_dir.glob('*'):
        with open(file, "rb") as f:
            recipe = f.read()
            filename = f"{file.name[:40]}... .pdf"
            msg.add_attachment(recipe, maintype='application', subtype='pdf', filename=filename)
    
    return msg

    
def send_email(translated_recipes: List[Recipe]):
    msg = EmailMessage()
    msg['Subject'] = f"Crisp Recipes Week {week_number + 1}"
    msg['From'] = sender_email
    msg['To'] = ", ".join(recipients)

    # add email body
    body = [f"{recipe.title}: {recipe.cooking_time_minutes} min\n\n" \
            for recipe in translated_recipes]
    body = "\n".join(body)
    msg.set_content(body)

    # add each recipe as attachment
    msg = add_text_attachment(msg)

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls() # secure the connection
        server.login(sender_email, sender_password)
        server.send_message(msg)
        print("Email sent successfully!")
    except Exception as e:
        print(f"Error sending email: {e}")
    finally:
        server.quit()


if __name__ == '__main__':
    send_email()