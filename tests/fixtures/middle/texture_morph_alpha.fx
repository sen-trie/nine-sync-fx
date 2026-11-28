float4x4 WVP:WORLDVIEWPROJECTION;
float4 TC:TOONCOLOR;
float4 VS(float4 p:POSITION):POSITION {return mul(p,WVP);}
float4 PS():COLOR {return float4(TC.a*.5,0,0,1);}
technique Model < string MMDPass="object"; > {pass Main {VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();}}
