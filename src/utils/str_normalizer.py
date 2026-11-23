def clean_data(data):
    '''
        data: data = {
        "contact_name": "  Carlos\t Martinez  Ival ",
        "contact_role": " CTO\n ",
        "interested_topics": [
            " AI   Automation ",
            " Backend\tDevelopment "
        ]
    }

    result 

    {
        "contact_name": "Carlos Martinez Ival",
        "contact_role": "CTO",
        "interested_topics": [
            "AI Automation",
            "Backend Development"
        ]
    }
    '''
    if isinstance(data, str):
        return " ".join(data.split())

    if isinstance(data, list):
        return [clean_data(item) for item in data]

    if isinstance(data, dict):
        return {
            key: clean_data(value)
            for key, value in data.items()
        }

    return data