from requests.auth import AuthBase
import time
import json
import requests
import hashlib
import hmac
from future.builtins import bytes
from future.standard_library import hooks
with hooks():  # Python 2/3 compat
    from urllib.parse import urlparse
from urllib import parse

# for rest API which need auth
class APIKeyAuthWithExpires(AuthBase):

    """Attaches API Key Authentication to the given Request object. This implementation uses `expires`."""

    def __init__(self, apiKey, apiSecret):
        """Init with Key & Secret."""
        self.apiKey = apiKey
        self.apiSecret = apiSecret

    def __call__(self, r):
        """
        Called when forming a request - generates api key headers. This call uses `expires` instead of nonce.

        This way it will not collide with other processes using the same API Key if requests arrive out of order.
        """
        # modify and return the request
        expires = int(round(time.time()) + 600)*1000  # 60s grace period in case of clock skew
        #expires = 1576477289000
        r.headers['api-expires'] = str(expires)
        r.headers['api-key'] = self.apiKey
        #print(str(r.body,encoding = "utf-8"))

        parsedURL = urlparse(r.url)
        #path = parsedURL.path
        #print(parsedURL) #ParseResult(scheme='http', netloc='192.168.0.71:7000', path='/v1/api/pc/order/query', params='', query='asset=BTC&symbol=BTC_USD&count=100', fragment='')

        qsencoded = parsedURL.query   #'asset=BTC&symbol=BTC_USD&count=100'
        data = r.body  #post 里面才有

        if data:   #参数是以 data 形式的请求，用body做签名
            if isinstance(data, str):  # request 参数用的data
                query_dict = dict(parse.parse_qsl(data))
                data_str = json.dumps(query_dict,sort_keys = True).replace(' ', '')  #json.dumps返回带空格的字符串，确保去除空格
            elif isinstance(data, (bytes, bytearray)):   # request 参数用的json
                data_str = str(data,encoding = "utf-8")   #r.body 是bytes
            else:
                print('r.body not type of str nor bytes, retun None')

            # 对字段排序，先转为dict, 再排序、转回str
            d = json.loads(data_str)
            #print(d)
            data_str1 = json.dumps(d,sort_keys = True).replace('\\', '')  #json.dumps返回带空格的字符串，确保去除反斜杠
            data_str = data_str1.replace(' ', '')  #json.dumps返回带空格的字符串，确保去除空格
        elif qsencoded:     #参数是以 query string 形式的请求，使用query string签名
            #path = path + '?' + parsedURL.query
            query_dict = dict(parse.parse_qsl(qsencoded))

            data_str1 = json.dumps(query_dict,sort_keys = True).replace('\\', '')  #json.dumps返回带空格的字符串，确保去除反斜杠
            data_str = data_str1.replace(' ', '')  #json.dumps返回带反斜杠的字符串，确保去除空格      
        else:
            print('error:has no parameters')
        #print('data_str:'+data_str)

        message = self.apiKey +  str(expires) + data_str
        #print('message str:'+ message)
        #print('secret str:'+ self.apiSecret)
        signature = hmac.new(bytes(self.apiSecret, 'utf8'), bytes(message, 'utf8'), digestmod=hashlib.sha256).hexdigest()
        #print('signature str:'+signature)
        r.headers['api-signature'] = signature
        #r.headers['api-signature'] = generate_gte_signature(self.apiKey,self.apiSecret,  expires,  data_str or '')
        #print(self.apiKey)
        #print(str(expires))
        #print(r.headers['api-signature'])
        
        return r

# Generates an API signature.
# A signature is HMAC_SHA256(secret, verb + path + nonce + data), hex encoded.
# Verb must be uppercased, url is relative, nonce must be an increasing 64-bit integer
# and the data, if present, must be JSON without whitespace between keys.
#
# For example, in psuedocode (and in real code below):
#
# expires=1416993995705
# data_str must be str, {"symbol":"XBTZ14","quantity":1,"price":395.01}
# signature = HEX(HMAC_SHA256(secret, 'POST/api/v1/order1416993995705{"symbol":"XBTZ14","quantity":1,"price":395.01}'))
# 参数均为str，data_str已经排好序了
def generate_gte_signature(apikey, secret, expires, data_str):  
    """Generate a request signature compatible with GTE."""
    # print "Computing HMAC: %s" % verb + path + str(nonce) + data
    message = apikey +  str(expires) + data_str
    print('message str:'+ message)

    signature = hmac.new(bytes(secret, 'utf8'), bytes(message, 'utf8'), digestmod=hashlib.sha256).hexdigest()
    print('signature str:'+signature)

    return signature


#def generate_bitmex_signature(apikey, secret, expires):  
def generate_bitmex_signature(): 
    """Generate a request signature compatible with GTE."""
    # print "Computing HMAC: %s" % verb + path + str(nonce) + data
    apikey = 'uD-ntEgSYjGTJNSt5p7yKac3'
    secret = 'RoJLx3u_bVRvNRAOQxvPv3dUuSMiqjh6q8o6z68uvyx_g1Xs'
    expires = int(round(time.time()) + 60)*1000  # 60s grace period in case of clock skew

    message = 'GET/realtime' +  str(expires) 
    print('apikey:'+ apikey)
    print('secret:'+ secret)
    print('expires:'+ str(expires))
    
    signature = hmac.new(bytes(secret, 'utf8'), bytes(message, 'utf8'), digestmod=hashlib.sha256).hexdigest()
    print('signature str:'+signature)

    return signature

def test():
    #auth = APIKeyAuthWithExpires('mTCemQTEgxxslsyoutnegFfn','fpVuaRncXaPtpjpSVEHtjYwRaiFzkgBIxDfUysVjOsaMcFYVHcNSlJxoYRfntKXd') # gte.io
    auth = APIKeyAuthWithExpires('PNEsWEDhwvsNVkfZIBsRIoAv','WpcBcZMduJIyYldMGjSHWFauMNqlvVyJCGBHSuEAEovsVPzkpAGSfRisLazPbcTP')  # 内网

    session = requests.Session()
    # These headers are always sent
    #session.headers.update({'user-agent': 'liquidbot-' + constants.VERSION})
    session.headers.update({'content-type': 'application/json'})
    session.headers.update({'accept': 'application/json;charset=UTF-8'})
    # Make the request
    response = None
    #url = 'http://192.168.0.71:7000/v1/api/pc/order/query'
    #url = 'http://61.173.81.188:12000/v1/api/pc/order/query'
    #url = 'http://192.168.0.71:7000/v1/api/pc/order/query'
    url ="http://hupa.7766.org:12000/v1/api/pc/order/query"

    params = {
                'filter': json.dumps({"status":["1"]}).replace(' ', ''),  #值是str,注意：这里必须要去空格！因为服务端会用原字符串去签名。
                #'filter': {"status":"1","order_id":"123000001"},  #错误的表达方式，query会被encode为 query='filter=status&filter=order_id&asset=BTC&symbol=BTC_USD&count=100'
                'asset':'BTC',
                'symbol': 'BTC_USD',
                'count':'100'
            }
    try:
        print("sending req to %s: %s" % (url, json.dumps(params  or '')))
        req = requests.Request('GET', url, auth=auth, params = params)
        prepped = session.prepare_request(req)
        response = session.send(prepped, timeout=20)
        print(response.text)
        # Make non-200s throw
        #response.raise_for_status()

    except requests.exceptions.HTTPError as e:
        if response is None:
            raise e

if __name__ == "__main__":
    #generate_bitmex_signature()
    test()
   
