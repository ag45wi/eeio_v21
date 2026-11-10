from flask import session, url_for
from flask_mail import Mail, Message #pip install flask_mail
import pandas as pd
from flask_mysqldb import MySQL
from dotenv import load_dotenv
import os
import matplotlib.pyplot as plt
import numpy as np
import MySQLdb
from datetime import datetime
import fnmatch
import re
import itsdangerous
import hashlib

cwd=os.getcwd()

# Setup a serializer
load_dotenv()
serializer = itsdangerous.URLSafeTimedSerializer(os.environ["APP_KEY"])
VERIFY_TIME_LIMIT=24 #in hour


#INIT--------------------------------------------------------------------------
def db_init(app):
    load_dotenv()

    app.config['MYSQL_HOST'] = 'localhost'
    app.config['MYSQL_USER'] = os.environ["DB_USER"]
    app.config['MYSQL_PASSWORD'] = os.environ["DB_PASSWORD"]
    app.config['MYSQL_DB'] = os.environ["DB_NAME"]

    #note: check services.msc in Windows to see if MySQL is running

    mysql = MySQL(app)
    return mysql

def tbl_to_df(db, query):
    #import MySQLdb

    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(query)

    df = pd.DataFrame(cursor.fetchall())
    #print("tbl_to_df::df", df.head())

    return df

def get_hash_password(password):
    #https://pagorun.medium.com/password-encryption-in-python-securing-your-data-9e0045e039e1


    hash_object = hashlib.sha256()# Convert the password to bytes and hash it
    hash_object.update(password.encode())# Get the hex digest of the hash
    hash_password = hash_object.hexdigest()
    #print(hash_password)

    return hash_password


#MAIL------------------------------------------------------------------------------------

# Generate a token
def generate_token(email):
    return serializer.dumps(email, salt="email-confirmation-salt")

# Confirm the token
def confirm_token(token, expiration=3600*VERIFY_TIME_LIMIT):  # Token valid for X hour
    try:
        email = serializer.loads(token, salt="email-confirmation-salt", max_age=expiration)
        return email
    except itsdangerous.SignatureExpired:
        return None
    except itsdangerous.BadSignature:
        return None

def send_verification_email(mail, user_email):
    print("inside send_verification_email::")

    token = generate_token(user_email)
    verification_url = url_for('verify_email', token=token, _external=True)
# Internal development comment removed for public distribution.
    print("verification_url", verification_url)
    msg = Message(
        subject="Email Verification",
        recipients=[user_email],
        body=f"Thank you for signing up at EEIO Web App. \nPlease verify your email by clicking this link: {verification_url} \nThe link will be expired in {VERIFY_TIME_LIMIT} hours.",
        sender="no-reply@eeio.inovasitiadahenti.com",
    )
    mail.send(msg)

    return f"Please check your email {user_email} and click the given verification link"

def verify_email_byToken(db, token):
    email = confirm_token(token)

    message= "The verification link is invalid or expired."
    status='fail'

    if email:
        cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute(f"SELECT * FROM user WHERE email = '{email}'")
        user = cursor.fetchone()

        #user = User.query.filter_by(email=email).first()
        if user:
            #user.email_verified = True
            #db.session.commit()
            userid=user["userid"]
            message, status=verify_user(db, userid)

            #message= "Email verified successfully!"
            #status='success'
        else:
            message= "Cannot find user for this token. Please re-register"
            status='fail'

    return message, status

#LOGIN----------------------------------------------------------------------------------------------
def login_prompt(db, userid, password):
    message=''
    status=''

    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM user WHERE userid = % s AND password = % s', (userid, password, ))
    user = cursor.fetchone()
    if user:
        if user['request_status']=="unverified":
            message = f"Your request status is still unverified; Verify the confirmation link in your email ({user['email']})"
            status="fail"
            return message, status

        print(f"login ok {userid}")
        session['loggedin'] = True
        session['userid'] = user['userid']
        session['name'] = user['name']
        session['email'] = user['email']
        message = 'Logged in successfully !'
        #return redirect(url_for('nav_home'))
        status="success"
    else:
        print(f"login NOT ok {userid}")
        message = 'Please enter correct email / password !'
        status="fail"

    return message, status


def login_register(db, mail, userID, userName, password, email):
    print("inside login_register")
    message=''
    status=''

    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(f"SELECT * FROM user WHERE userid = '{userID}' or email='{email}'")
    account = cursor.fetchone()
    print("account", account)

    if account:
        message = f'Account with userid: {userID} or email: {email} already exists !'
        status="fail"
    elif not re.match(r'[^@]+@[^@]+\.[^@]+', email):
        message = 'Invalid email address !'
        status="fail"
    elif not userName or not password or not email:
        message = 'Please fill out the form !'
    else:
        query=f"INSERT INTO user (userid, name, email, password) VALUES ('{userID}', '{userName}', '{email}', '{password}')"
        print("query", query)
        cursor.execute(query)
        db.connection.commit()

        message=send_verification_email(mail, email)

        #message = 'You have successfully registered, pending verification by the Administrator'
        status="success"

    return message, status

def login_changePassword(db, password_old, password_new):

    userid=session['userid']
    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(f"SELECT * FROM user WHERE userid = '{userid}'")
    user = cursor.fetchone()

    #print(user['password'], password_old)

    if user['password'] == password_old:
        query = f"UPDATE user set password='{password_new}' WHERE userid='{userid}'"
        #print("query change_password::", query)
        cursor.execute(query)
        db.connection.commit()
        message = 'You have successfully changed the password !'
        status="success"
    else:
        #print("Cannot change_password")
        message = 'Mismatch between the stored password and entered old password !'
        status="fail"

    return message, status

def get_table_user(db):

    #query =f"select * from user WHERE request_status<>'verified'"
    query =f"select userid, name, email, request_status, create_time from user ORDER BY request_status, create_time"
    print("query", query)
    df=tbl_to_df(db, query)
    #print("df", df)
    return df

def verify_user(db, userid):
    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)

    query_chk =f"SELECT * from user WHERE userid='{userid}'"
    cursor.execute(query_chk)
    user = cursor.fetchone()
    if (user and user['request_status']=="verified"):
        message=f"Request status of '{userid}' has been previously verified"
        status="success"
        return message, status

    query =f"UPDATE user set request_status='verified' WHERE userid='{userid}'"
    try:
        cursor.execute(query)
        db.connection.commit()

        message=f"Request status of '{userid}' has been verified"
        status="success"
    except:
        message=f"Request status of '{userid}' cannot be verified"
        status="fail"

    return message, status

def delete_user(db, userid):
    query =f"DELETE FROM user WHERE userid='{userid}'"

    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        cursor.execute(query)
        db.connection.commit()

        message=f"User '{userid}' has been deleted"
        status="success"
    except:
        message=f"User '{userid}' cannot be deleted"
        status="fail"

    return message, status


def execute_query(db, query, isBatch=False, data_to_insert=None):
    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)
    if (isBatch):
        if (data_to_insert):
            cursor.executemany(query, data_to_insert)
        else:
            cursor.executemany(query)
    else:
        if (data_to_insert):
            cursor.execute(query, data_to_insert)
        else:
            cursor.execute(query)

    db.connection.commit()