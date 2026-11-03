def page_numbers(page, pages):
    selected=sorted({1,pages,*range(max(1,page-2),min(pages,page+2)+1)})
    result=[]
    for number in selected:
        if result and number-result[-1]>1:
            if number-result[-1]==2:result.append(number-1)
            else:result.append(None)
        result.append(number)
    return result
